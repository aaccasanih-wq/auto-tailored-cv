"""CLI entrypoint for auto-tailored-cv.

Usage:
    python run.py all                 # extract + tailor + render (HTML + PDF)
    python run.py extract            # only scrape LinkedIn into jobs/
    python run.py tailor             # only tailor already-extracted jobs
    python run.py review <job_slug>  # editable review of a job's cv.html
    python run.py all --new          # only process jobs saved since last run
    python run.py all <url>          # process one specific job URL (positional)
    python run.py all --job <url>    # same, explicit flag form
    python run.py all --dry-run      # show what would be processed, no LLM calls
    python run.py all --force        # re-process already-processed jobs
    python run.py all --last 1       # only the most recently saved job
    python run.py all --scraper playwright    # default; Playwright MCP
    python run.py all --scraper browsermcp     # legacy fallback; Browser MCP
    python run.py all --legacy-docx   # render via docx_writer + LibreOffice
    python run.py manual --title "Data Engineer" --company "Acme" \
        --description-file offer.txt  # tailor from a pasted (non-LinkedIn) offer

Safety: when `all` or `tailor` (without --dry-run) would target MORE THAN ONE
job and `--force` is set, the CLI prompts `Apply to N jobs? [y/N]` on stdin.
Decline (the default) aborts with rc=1. This prevents accidental bulk
re-tailorization when an agent or user omits `--job`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import ensure_dirs, settings
from src.extract.linkedin_scraper import (
    SavedJob,
    _job_id_from_url,
    _normalize_job_url,
    extract_saved_jobs,
    save_jobs_json,
)
from src.profile.cv_reader import read_cv
from src.profile.preferences import load_user_preferences
from src.render import html_renderer, pdf_renderer
from src.tailor.cv_rewriter import save_tailored_json, tailor_cv
from src.tailor.evaluator import evaluate, save_evaluation_json
from src.tailor.job_summarizer import (
    load_job_summary,
    save_job_summary,
    summarize_job,
)
from src.tailor.llm_client import make_client
from src.tailor.prompts import JobInfo
from src.tailor.repair import repair_cv, save_repaired_json
from src.utils.logging import configure_logging, get_logger
from src.utils.slugify import job_output_path, slugify

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Cache + registry helpers                                                     #
# --------------------------------------------------------------------------- #
#
# Besides the per-job cache files (`jobs/<id>.json`) there is a lightweight
# registry (`jobs/_index.json`) keyed by the CANONICAL LinkedIn job id. Its job:
#   - know, without reading every cache file, whether a CV was already generated
#     for a job id (regardless of which URL variant the user pasted);
#   - preserve the "already generated" state when `all` re-extracts saved jobs
#     (the old code reset `tailored=False` on every extraction, silently
#     re-tailorizing everything).


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _index_path() -> Path:
    return settings.jobs_dir / "_index.json"


def _load_index() -> dict[str, dict]:
    p = _index_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(index: dict[str, dict]) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _index_key(job: SavedJob) -> str:
    return job.job_id or _job_id_from_url(job.url) or _normalize_job_url(job.url) or job.url


def _build_index_record(
    job: SavedJob,
    prev: dict,
    *,
    tailored: bool,
    cv_generated_at: str = "",
    cv_pdf_path: str = "",
) -> dict:
    """Build a registry record for `job`, merging with the previous record.

    The registry NEVER downgrades an existing "already generated a CV" state:
    if the previous record had `tailored=true` and the new value is `false`
    (e.g. a plain re-extraction), the previous state wins.
    """
    effective_tailored = bool(prev.get("tailored", False)) or bool(tailored)
    return {
        "job_id": job.job_id or prev.get("job_id", ""),
        "url_original": job.url,
        "url_canonica": _normalize_job_url(job.url),
        "title": job.title or prev.get("title", ""),
        "company": job.company or prev.get("company", ""),
        "location": job.location or prev.get("location", ""),
        "saved_at_iso": job.saved_at_iso or prev.get("saved_at_iso", ""),
        "tailored": effective_tailored,
        "status": "done" if effective_tailored else "pending",
        "cv_generated_at": cv_generated_at or prev.get("cv_generated_at", ""),
        "cv_pdf_path": cv_pdf_path or prev.get("cv_pdf_path", ""),
        "last_seen_at": prev.get("last_seen_at", "") or _now_iso(),
    }


def _upsert_index(
    job: SavedJob,
    *,
    tailored: bool,
    cv_generated_at: str = "",
    cv_pdf_path: str = "",
) -> None:
    """Upsert the registry record for a job (never downgrades existing state)."""
    key = _index_key(job)
    index = _load_index()
    prev = index.get(key) or {}
    index[key] = _build_index_record(
        job, prev, tailored=tailored, cv_generated_at=cv_generated_at, cv_pdf_path=cv_pdf_path
    )
    _save_index(index)


def _backfill_index() -> None:
    """Populate the registry from the existing per-job cache files.

    Needed so that jobs cached before this feature (already-tailored ones
    included) get registry entries without waiting for their next extraction —
    otherwise the "already generated a CV" guard would not fire for them.
    Idempotent: skips files whose record is already present and unchanged.
    """
    if not settings.jobs_dir.exists():
        return
    index = _load_index()
    dirty = False
    for f in sorted(settings.jobs_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        job = SavedJob(
            title=data.get("title", "") or "",
            url=data.get("url", "") or "",
            company=data.get("company", "") or "",
            location=data.get("location", "") or "",
            saved_at_iso=data.get("saved_at_iso", "") or "",
            description=data.get("description", "") or "",
            job_id=data.get("job_id", "") or "",
            warnings=data.get("warnings", []) or [],
        )
        key = _index_key(job)
        record = _build_index_record(
            job,
            index.get(key) or {},
            tailored=bool(data.get("tailored", False)),
            cv_generated_at=data.get("cv_generated_at", ""),
            cv_pdf_path=data.get("cv_pdf_path", ""),
        )
        if index.get(key) != record:
            index[key] = record
            dirty = True
    if dirty:
        _save_index(index)


def _generated_cv_info(job: SavedJob) -> dict:
    """Return generation metadata (date + pdf path) for a job from the registry,
    falling back to the per-job cache file."""
    key = _index_key(job)
    rec = _load_index().get(key) or {}
    if rec.get("cv_generated_at") or rec.get("cv_pdf_path"):
        return rec
    cache = _job_cache_path(job)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return rec


def _job_cache_path(job: SavedJob) -> Path:
    key = (job.job_id or job.url).rstrip("/").split("/")[-1] or "untitled"
    safe = "".join(c for c in key if c.isalnum() or c in "-_")
    if not safe:
        safe = "untitled"
    return settings.jobs_dir / f"{safe}.json"


def _is_processed(job: SavedJob) -> bool:
    """A job is "already processed" if its cache file has tailored=true."""
    cache = _job_cache_path(job)
    if not cache.exists():
        return False
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("tailored", False))


def _save_job_cache(
    job: SavedJob,
    tailored: bool,
    cv_generated_at: str = "",
    cv_pdf_path: str = "",
) -> None:
    cache = _job_cache_path(job)
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload = job.to_dict()
    payload["tailored"] = tailored
    if cv_generated_at:
        payload["cv_generated_at"] = cv_generated_at
    if cv_pdf_path:
        payload["cv_pdf_path"] = cv_pdf_path
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _upsert_index(job, tailored=tailored, cv_generated_at=cv_generated_at, cv_pdf_path=cv_pdf_path)


def _upsert_job_cache(job: SavedJob) -> None:
    """Merge a freshly-scraped job into its cache file WITHOUT losing the
    "already generated a CV" state.

    Used by `do_extract`: a re-extraction must refresh title/company/description
    but must NOT reset `tailored` (or drop `cv_generated_at` / `cv_pdf_path`) of
    a job that already has a CV — otherwise every `all` run would silently
    re-tailorize everything.
    """
    cache = _job_cache_path(job)
    prev: dict = {}
    if cache.exists():
        try:
            prev = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    payload = job.to_dict()
    payload["tailored"] = bool(prev.get("tailored", False))
    for key in ("cv_generated_at", "cv_pdf_path"):
        if prev.get(key):
            payload[key] = prev[key]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _upsert_index(
        job,
        tailored=payload["tailored"],
        cv_generated_at=payload.get("cv_generated_at", ""),
        cv_pdf_path=payload.get("cv_pdf_path", ""),
    )


def _load_cached_jobs() -> list[SavedJob]:
    jobs: list[SavedJob] = []
    if not settings.jobs_dir.exists():
        return jobs
    for f in sorted(settings.jobs_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        jobs.append(SavedJob(
            title=data.get("title", "") or "",
            url=data.get("url", "") or "",
            company=data.get("company", "") or "",
            location=data.get("location", "") or "",
            saved_at_iso=data.get("saved_at_iso", "") or "",
            description=data.get("description", "") or "",
            job_id=data.get("job_id", "") or "",
            warnings=data.get("warnings", []) or [],
            saved_order=int(data.get("saved_order", -1) or -1),
        ))
    return jobs


def _sort_by_recency(jobs: list[SavedJob]) -> list[SavedJob]:
    """Sort cached jobs by how recently they were saved.

    - Jobs with a `saved_at_iso` timestamp sort first, most recent first.
    - Jobs without a timestamp (old cache) sort after, using `saved_order`
      (1 = most recently saved in the latest extraction, because LinkedIn
      orders its saved-jobs listing by recency).
    """
    dated = [j for j in jobs if (j.saved_at_iso or "").strip()]
    undated = [j for j in jobs if not (j.saved_at_iso or "").strip()]
    dated.sort(key=lambda j: j.saved_at_iso, reverse=True)
    undated.sort(key=lambda j: j.saved_order)
    return dated + undated


# --------------------------------------------------------------------------- #
# Pipeline stages                                                             #
# --------------------------------------------------------------------------- #


def do_extract(
    target_url: str | None = None,
    scraper_backend: str = "playwright",
) -> list[SavedJob]:
    """Run the LinkedIn scrape and cache results."""
    log.info("extracting saved jobs from LinkedIn via %s …", scraper_backend)
    try:
        jobs = asyncio.run(
            extract_saved_jobs(saved_jobs_url=target_url or "", backend=scraper_backend)
        )
    except Exception as e:
        log.error("extraction failed: %s", e)
        return []
    if not jobs:
        log.warning("no saved jobs were extracted")
        return []
    for job in jobs:
        # Upsert preserves the "already generated a CV" state (see
        # _upsert_job_cache) instead of resetting tailored=False on every run.
        _upsert_job_cache(job)
    save_jobs_json(jobs, settings.jobs_dir / "_all_saved_jobs.json")
    log.info("extracted %d saved jobs", len(jobs))
    return jobs


def _resolve_job_folder(job: SavedJob) -> Path:
    return job_output_path(
        settings.output_dir, job.title, job.company or "company",
    )


# --------------------------------------------------------------------------- #
# "Manual" (pasted, non-LinkedIn) job offers                                   #
# --------------------------------------------------------------------------- #


def _derive_title_from_description(description: str) -> str:
    """Derive a job title from the first non-empty, non-heading line of a pasted
    offer description (so `manual` works even without --title)."""
    for line in (description or "").splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:80]
    return "Oferta laboral"


def _manual_job(
    title: str,
    company: str,
    location: str,
    description: str,
) -> SavedJob:
    """Build a SavedJob from a pasted (non-LinkedIn) job offer.

    Uses a synthetic stable id (``manual-<title>-<company>``) so repeated runs
    of the same pasted offer dedup against the jobs/ cache just like LinkedIn
    jobs, without ever colliding with a real LinkedIn job id.
    """
    sid = slugify(title) or "oferta"
    cid = slugify(company) or "empresa"
    key = f"manual-{sid}-{cid}"
    return SavedJob(
        title=title,
        url=f"manual://{key}",
        company=company,
        location=location,
        saved_at_iso=_now_iso(),
        description=description,
        job_id=key,
    )


def _render_job_html_pdf(
    tailored_json: dict,
    out_dir: Path,
) -> tuple[Path | None, Path | None]:
    """Render the tailored JSON to cv.html + cv.pdf via Jinja2 + Playwright."""
    try:
        # Supplement the analysis.json with `personal_info` (name/contact/links)
        # from the base CV so the rendered page header is complete.
        base_profile = read_cv(settings.base_cv_path)
        payload = dict(tailored_json)
        payload.setdefault("personal_info", base_profile.personal_info.to_dict())
        # Make sure each section has a 'type' so the Jinja template renders the
        # right generic blocks. Default-derive from base CV by title match.
        base_by_title = {s.title: s.type for s in base_profile.sections}
        for s in payload.get("sections", []) or []:
            s.setdefault("type", base_by_title.get(s.get("title", ""), ""))
        html_path = html_renderer.render(payload, out_dir)
    except Exception as e:
        log.error("html rendering failed: %s", e)
        return None, None
    try:
        pdf_result = pdf_renderer.render(html_path)
        if not pdf_result.success:
            log.warning("pdf generation failed: %s", pdf_result.error)
            return html_path, None
        return html_path, pdf_result.pdf_path
    except Exception as e:
        log.error("pdf generation raised: %s", e)
        return html_path, None


def _render_job_legacy_docx(
    tailored_json: dict,
    out_dir: Path,
) -> Path | None:
    """Render via the legacy python-docx + LibreOffice path (behind --legacy-docx).

    This path needs the OLD base_cv.docx template — it is kept for backwards
    compatibility only and does NOT work with the new YAML base CV. If the
    configured base_cv_path is not a .docx, it is skipped with a clear log.
    """
    if settings.base_cv_path.suffix.lower() != ".docx":
        log.warning(
            "--legacy-docx requires a base_cv.docx template, but "
            "BASE_CV_PATH=%s (the new YAML format). Skipping legacy render.",
            settings.base_cv_path,
        )
        return None
    try:
        from src.render.legacy.docx_writer import write_tailored_docx
        from src.render.legacy.pdf_converter import convert_docx_to_pdf
    except Exception as e:
        log.error("legacy docx path unavailable: %s", e)
        return None
    docx_path = out_dir / "cv.docx"
    try:
        write_tailored_docx(settings.base_cv_path, tailored_json, docx_path)
    except Exception as e:
        log.error("legacy docx writer failed: %s", e)
        return None
    pdf_result = convert_docx_to_pdf(docx_path, out_dir)
    if not pdf_result.success:
        log.warning("legacy pdf conversion failed: %s", pdf_result.error)
    return docx_path


def _tailor_one(
    client,
    base_profile,
    job: SavedJob,
    dry_run: bool,
    legacy_docx: bool = False,
    force: bool = False,
    user_preferences: str = "",
) -> Path | None:
    """Run summarize + tailor (+ evaluate + repair) + render for a single job.
    Returns the output folder path, or None on failure."""
    out_dir = _resolve_job_folder(job)

    if dry_run:
        log.info("[dry-run] would tailor for '%s' at %s -> %s", job.title, job.company, out_dir)
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "job_description.txt").write_text(job.description, encoding="utf-8")

    # --- Job summary: computed ONCE per offer, cached; re-computed on --force.
    # The RAW description never reaches tailor/evaluate/repair — only this
    # summary does (biggest token saving of the redesign).
    summary_path = out_dir / "job_summary.json"
    job_summary = load_job_summary(summary_path) if not force else None
    if job_summary is None:
        log.info("summarizing job description for '%s' at %s", job.title, job.company)
        job_summary = summarize_job(
            client,
            JobInfo(title=job.title, company=job.company, description=job.description),
        )
        save_job_summary(job_summary, summary_path)

    job_info = JobInfo(
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        summary=job_summary,
    )

    log.info("tailoring CV for '%s' at %s", job.title, job.company)
    tailored = tailor_cv(client, base_profile, job_info, user_preferences=user_preferences)
    save_tailored_json(tailored, out_dir / "analysis.json")
    if tailored.tailored_json is None or not tailored.tailored_json.get("sections"):
        log.error("tailor produced empty/null sections for %s; skipping", job.title)
        return None

    final_json = tailored.tailored_json
    if settings.enable_evaluation:
        log.info("evaluating tailored CV for '%s'", job.title)
        evaluation = evaluate(
            client, base_profile, job_info, tailored.tailored_json,
            user_preferences=user_preferences,
        )
        # `url_tampered` issues are deterministic: the orchestrator re-injects
        # protected URLs from the base CV AFTER the tailor pass, so whatever the
        # tailor emitted (or didn't) gets overwritten byte-identical anyway. We
        # never need to spend a repair LLM call on them. Same for `format` shape
        # issues — `_validate_shape` already flagged them deterministically and
        # the repair pass can't reliably fix shape. Filter them out before
        # deciding whether to invoke repair.
        DETERMINISTIC_ISSUE_TYPES = {"url_tampered", "format"}
        semantic_issues = [
            i for i in evaluation.issues
            if i.get("type") not in DETERMINISTIC_ISSUE_TYPES
        ]
        needs_repair = (
            evaluation.verdict == "fail"
            or any(i.get("severity") == "high" for i in semantic_issues)
        )
        if needs_repair and semantic_issues:
            log.info("repairing %d semantic issue(s) for '%s'", len(semantic_issues), job.title)
            repaired = repair_cv(
                client, base_profile, tailored.tailored_json, semantic_issues,
                user_preferences=user_preferences,
            )
            if repaired.repaired_json:
                final_json = repaired.repaired_json
                save_repaired_json(repaired, out_dir / "analysis_repaired.json")
        save_evaluation_json(evaluation, out_dir / "evaluation.json")
    else:
        log.info(
            "ENABLE_EVALUATION=false — skipping evaluate + repair for '%s' "
            "(nada verifica alucinaciones ni copiado literal de la oferta)",
            job.title,
        )

    if legacy_docx:
        log.info("rendering .docx/.pdf (legacy) for '%s'", job.title)
        _render_job_legacy_docx(final_json, out_dir)
    else:
        log.info("rendering cv.html/cv.pdf for '%s'", job.title)
        _render_job_html_pdf(final_json, out_dir)

    # Record generation metadata in the registry so the user (and the CLI) can
    # tell which offers already have a CV.
    cv_pdf = out_dir / "cv.pdf"
    cv_html = out_dir / "cv.html"
    if cv_pdf.exists():
        artifact = str(cv_pdf)
    elif cv_html.exists():
        artifact = str(cv_html)
    else:
        artifact = str(out_dir)
    _save_job_cache(
        job,
        tailored=True,
        cv_generated_at=_now_iso(),
        cv_pdf_path=artifact,
    )
    log.info("done: %s", out_dir)
    return out_dir


def do_tailor(
    jobs: list[SavedJob],
    dry_run: bool = False,
    force: bool = False,
    legacy_docx: bool = False,
) -> list[Path]:
    if not jobs:
        log.warning("no jobs to tailor")
        return []
    base_profile = read_cv(settings.base_cv_path)
    user_prefs = load_user_preferences(settings.preferences_path)
    log.info("base CV (%s): %d sections", settings.base_cv_path, len(base_profile.sections))
    if user_prefs:
        log.info("personal preferences loaded from %s", settings.preferences_path)
    if dry_run:
        client = None  # type: ignore[assignment]
    else:
        if not settings.is_configured:
            log.error(
                "LLM_API_KEY is not set. Copy .env.example to .env and paste "
                "your LLM provider API key."
            )
            return []
        client = make_client()

    outputs: list[Path] = []
    for i, job in enumerate(jobs, start=1):
        log.info("--- [%d/%d] %s @ %s ---", i, len(jobs), job.title, job.company)
        if not force and _is_processed(job):
            info = _generated_cv_info(job)
            when = f" el {info.get('cv_generated_at')}" if info.get("cv_generated_at") else ""
            where = f" -> {info.get('cv_pdf_path')}" if info.get("cv_pdf_path") else ""
            log.info(
                "ya hay un CV generado para esta oferta%s%s — saltando "
                "(usá --force para regenerarlo).",
                when, where,
            )
            continue
        try:
            out = _tailor_one(
                client, base_profile, job, dry_run=dry_run, legacy_docx=legacy_docx,
                force=force, user_preferences=user_prefs,
            )
            if out is not None:
                outputs.append(out)
        except Exception as e:
            log.error("tailoring failed for %s: %s", job.url, e)

    if outputs and not dry_run:
        log.info("generated %d CV(s):", len(outputs))
        for out in outputs:
            pdf = out / "cv.pdf"
            html = out / "cv.html"
            if pdf.exists():
                log.info("  • %s", pdf)
            elif html.exists():
                log.info("  • %s (html only)", html)
            else:
                log.info("  • %s", out)
        log.info(
            "If a result didn't convince you, edit it interactively with: "
            "`python run.py review <job_slug>`."
        )
    return outputs


# --------------------------------------------------------------------------- #
# Bulk-confirmation guard                                                     #
# --------------------------------------------------------------------------- #


def _confirm_bulk(job_count: int) -> bool:
    """Prompt the user (or driving agent) for y/N when about to process >1
    job under --force without --dry-run. Returns True to proceed, False to
    abort. Non-interactive streams (no TTY) abort unless `--yes` was passed
    (the caller handles that flag before calling here, so by the time we're
    invoked, --yes has already short-circuited us).
    """
    prompt = (
        f"About to (re)tailor {job_count} jobs in one go. "
        "This will make ~{n} LLM calls and can take several minutes.\n"
        "Proceed? [y/N] ".format(n=job_count * 4)
    )
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _needs_bulk_confirm(args: argparse.Namespace, job_count: int) -> bool:
    """Decide if we should block on the bulk confirm prompt. The guard fires
    only when ALL of these are true:
      - the command would touch >1 job (job_count > 1),
      - --force is set (otherwise --new/skip handles incremental guard),
      - --dry-run is NOT set (dry runs never call the LLM),
      - --yes was NOT explicitly passed.
    """
    if getattr(args, "dry_run", False):
        return False
    if not getattr(args, "force", False):
        return False
    if job_count <= 1:
        return False
    if getattr(args, "yes", False):
        return False
    return True


# --------------------------------------------------------------------------- #
# Commands                                                                    #
# --------------------------------------------------------------------------- #


def cmd_extract(args: argparse.Namespace) -> int:
    ensure_dirs()
    target = args.job or getattr(args, "job_url", None)
    jobs = do_extract(target_url=target, scraper_backend=args.scraper)
    if not jobs:
        return 1
    return 0


def _matches_job_url(job: SavedJob, norm_job: str, target_id: str) -> bool:
    """True if a cached job corresponds to a user-provided job URL.

    Matching is by canonical job id FIRST (so different URL variants of the same
    offer — saved, recommended, search, share link — all resolve to the same
    job), falling back to the historical URL-substring check.
    """
    if target_id and job.job_id and job.job_id == target_id:
        return True
    return norm_job in job.url or job.url in norm_job


def _already_generated(job_url: str) -> dict | None:
    """Look up the registry for a user-pasted job URL that already has a CV.
    Returns the record (with cv_generated_at / cv_pdf_path) or None."""
    target_id = _job_id_from_url(_normalize_job_url(job_url))
    if not target_id:
        return None
    rec = _load_index().get(target_id)
    if rec and rec.get("tailored"):
        return rec
    return None


def cmd_tailor(args: argparse.Namespace) -> int:
    ensure_dirs()
    _backfill_index()
    job_url = args.job or getattr(args, "job_url", None)
    jobs = _load_cached_jobs()
    if job_url:
        # Auto-detection: if this offer (by canonical job id) already has a CV,
        # tell the user instead of spending tokens regenerating it.
        existing = _already_generated(job_url)
        if existing and not args.force:
            when = f" el {existing.get('cv_generated_at')}" if existing.get("cv_generated_at") else ""
            where = f" en {existing.get('cv_pdf_path')}" if existing.get("cv_pdf_path") else ""
            log.info(
                "Ya generaste un CV para esta oferta%s%s — no la regenero. "
                "Si querés rehacerlo, agregá --force.",
                when, where,
            )
            return 0
        norm_job = _normalize_job_url(job_url)
        target_id = _job_id_from_url(norm_job)
        jobs = [j for j in jobs if _matches_job_url(j, norm_job, target_id)]
        if not jobs:
            log.error("no cached job matches --job %s", job_url)
            return 2
    if args.new:
        jobs = [j for j in jobs if not _is_processed(j)]
    if args.last and args.last > 0:
        # "última(s) oferta(s) guardada(s)" — sort by recency, take the newest N.
        jobs = _sort_by_recency(jobs)[: args.last]
        log.info("--last %d: procesando %d oferta(s) más recientemente guardada(s)", args.last, len(jobs))
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]
    if _needs_bulk_confirm(args, len(jobs)):
        log.warning(
            "tailor --force would re-process %d cached job(s) at once.", len(jobs),
        )
        log.warning(
            "If you meant a SINGLE job, re-run with `tailor --job <url> --force` "
            "or add `--yes` to this command to skip this prompt."
        )
        if not _confirm_bulk(len(jobs)):
            log.error("aborted by user; no jobs were processed.")
            return 1
    outputs = do_tailor(
        jobs, dry_run=args.dry_run, force=args.force, legacy_docx=args.legacy_docx,
    )
    log.info("produced %d tailored CV(s)", len(outputs))
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    ensure_dirs()
    _backfill_index()
    job_url = args.job or getattr(args, "job_url", None)
    # Auto-detection: if the pasted URL resolves (by canonical job id) to an
    # offer that already has a CV, don't even re-scrape it — unless --force.
    if job_url and not args.force and not args.dry_run:
        existing = _already_generated(job_url)
        if existing:
            when = f" el {existing.get('cv_generated_at')}" if existing.get("cv_generated_at") else ""
            where = f" en {existing.get('cv_pdf_path')}" if existing.get("cv_pdf_path") else ""
            log.info(
                "Ya generaste un CV para esta oferta%s%s — no la regenero. "
                "Si querés rehacerlo, agregá --force.",
                when, where,
            )
            return 0
    jobs = do_extract(target_url=job_url, scraper_backend=args.scraper)
    if args.new:
        jobs = [j for j in jobs if not _is_processed(j)]
    if args.last and args.last > 0:
        jobs = _sort_by_recency(jobs)[: args.last]
        log.info("--last %d: procesando %d oferta(s) más recientemente guardada(s)", args.last, len(jobs))
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]
    if _needs_bulk_confirm(args, len(jobs)):
        log.warning(
            "all --force would re-process %d job(s) at once.", len(jobs),
        )
        log.warning(
            "If you meant a SINGLE job, re-run with `all --job <url> --force` "
            "or add `--yes` to skip this prompt."
        )
        if not _confirm_bulk(len(jobs)):
            log.error("aborted by user; no jobs were processed.")
            return 1
    outputs = do_tailor(
        jobs, dry_run=args.dry_run, force=args.force, legacy_docx=args.legacy_docx,
    )
    log.info("produced %d tailored CV(s)", len(outputs))
    return 0


def cmd_manual(args: argparse.Namespace) -> int:
    """Tailor a CV from a PASTED (non-LinkedIn) job offer.

    The description comes from `--description <text>`, `--description-file
    <path>`, or stdin (piped). `--title`/`--company` are optional; when missing
    the title is derived from the first line of the description and the company
    defaults to "empresa". Runs the same summarize → tailor → evaluate → repair
    → render pipeline as `tailor`, without scraping LinkedIn.
    """
    ensure_dirs()
    _backfill_index()

    description = ""
    if getattr(args, "description", None):
        description = args.description.strip()
    elif getattr(args, "description_file", None):
        p = Path(args.description_file)
        if not p.exists():
            log.error("--description-file no encontrado: %s", args.description_file)
            return 2
        description = p.read_text(encoding="utf-8").strip()
    else:
        # Piped stdin (e.g. `./run.sh manual --title ... < offer.txt`).
        if not sys.stdin.isatty():
            description = sys.stdin.read().strip()
    if not description:
        log.error(
            "no se recibió una descripción de oferta. Usá --description \"...\", "
            "--description-file <path>, o pipeá el texto por stdin."
        )
        return 2

    title = (getattr(args, "title", "") or "").strip() or _derive_title_from_description(description)
    company = (getattr(args, "company", "") or "").strip() or "empresa"
    location = (getattr(args, "location", "") or "").strip()

    job = _manual_job(title, company, location, description)
    outputs = do_tailor(
        [job], dry_run=args.dry_run, force=args.force, legacy_docx=args.legacy_docx,
    )
    log.info("produced %d tailored CV(s)", len(outputs))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    ensure_dirs()
    from src.review.server import run_server
    # Resolve nested date/slug layout (post-restructure). `run_server` also
    # falls back to a direct output_dir / job_slug lookup, but we resolve
    # FIRST so bare slugs like "practicante-profesional-de-ia_canvia" work
    # transparently across date directories.
    resolved = _resolve_job_output_dir(args.job_slug)
    if resolved is not None:
        # Pass the resolved job_slug (relative to output_dir) so the server
        # opens the right directory.
        rel = resolved.relative_to(settings.output_dir)
        from pathlib import PurePosixPath
        rel_slug = PurePosixPath(*rel.parts)
        try:
            run_server(str(rel_slug), host=args.host, port=args.port)
            return 0
        except FileNotFoundError as e:
            log.error(str(e))
            return 1
        except KeyboardInterrupt:
            log.info("review server stopped.")
            return 0
    # Fallback: hand the slug straight to run_server (its own lookup).
    try:
        run_server(args.job_slug, host=args.host, port=args.port)
        return 0
    except FileNotFoundError as e:
        log.error(str(e))
        return 1
    except KeyboardInterrupt:
        log.info("review server stopped.")
        return 0


def _resolve_job_output_dir(job_slug: str) -> Path | None:
    """Resolve a job_slug to its output directory under output/.

    Accepts three forms:
      - "<date>/<slug>"  (e.g. "2026-07-23/practicante-profesional-de-ia_canvia")
      - "<date>_<slug>"  (legacy flat form, e.g. "2026-07-23_practicante-..._canvia")
      - "<slug>"         (bare slug; searches across date directories)

    Returns None if no matching directory exists.
    """
    out_root = settings.output_dir
    # 1. Direct nested path  "<date>/<slug>".
    cand = out_root / job_slug
    if cand.is_dir():
        return cand
    # 2. Legacy flat form  "<date>_<slug>"  →  "<date>/<slug>".
    if "_" in job_slug and len(job_slug) >= 11 and job_slug[4] == "-" and job_slug[7] == "-":
        date_part = job_slug[:10]
        slug_part = job_slug[11:]
        cand2 = out_root / date_part / slug_part
        if cand2.is_dir():
            return cand2
    # 3. Bare slug: search across date dirs.
    if "/" not in job_slug:
        for date_dir in sorted(out_root.iterdir()) if out_root.exists() else []:
            if not date_dir.is_dir():
                continue
            cand3 = date_dir / job_slug
            if cand3.is_dir():
                return cand3
    return None


def _list_job_slugs() -> list[tuple[str, str]]:
    """Walk output/<date>/<slug>/ and return [(job_slug, has_pdf_marker)].

    job_slug is returned in the "<date>/<slug>" form so it can be passed
    back to `review` or used as a stable identifier.
    """
    out_root = settings.output_dir
    results: list[tuple[str, str]] = []
    if not out_root.exists():
        return results
    for date_dir in sorted(out_root.iterdir()):
        if not date_dir.is_dir():
            continue
        # Skip non-date dir names defensively.
        name = date_dir.name
        if len(name) != 10 or name[4] != "-" or name[7] != "-":
            continue
        for slug_dir in sorted(date_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            job_slug = f"{date_dir.name}/{slug_dir.name}"
            pdf = slug_dir / "cv.pdf"
            if pdf.exists():
                results.append((job_slug, "PDF"))
            else:
                results.append((job_slug, "   "))
    # Also include any legacy flat folders (<date>_<slug>) that weren't migrated.
    for flat in sorted(out_root.iterdir()):
        if not flat.is_dir():
            continue
        n = flat.name
        if len(n) >= 11 and n[4] == "-" and n[7] == "-" and "_" in n:
            results.append((n, "PDF" if (flat / "cv.pdf").exists() else "   "))
    return results


def cmd_list(args: argparse.Namespace) -> int:
    """List available job slugs (for use with `review <job_slug>`)."""
    ensure_dirs()
    rows = _list_job_slugs()
    if not rows:
        log.info("no tailored CVs found under %s", settings.output_dir)
        return 0
    log.info("%d tailored CV(s) under %s:", len(rows), settings.output_dir)
    print(f"{'PDF':6}  job_slug")
    print(f"{'---':6}  -------")
    # Suppress duplicate (date-nested then legacy) entries defensively.
    printed: set[str] = set()
    for slug, marker in rows:
        if slug in printed:
            continue
        printed.add(slug)
        print(f"{marker:6}  {slug}")
    print("\nEdit one in the browser with: python run.py review <job_slug>")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """Open a real Chromium window with the persistent profile. The user logs
    into LinkedIn manually; when they close the browser (Cmd+Q / window close),
    the cookies persist into .playwright-profile/ for subsequent headless runs.
    """
    ensure_dirs()
    from playwright.sync_api import sync_playwright

    profile_path = settings.playwright_user_data_dir
    if not Path(profile_path).is_absolute():
        from src.config import PROJECT_ROOT
        profile_path = str(PROJECT_ROOT / settings.playwright_user_data_dir)

    target_url = args.url or settings.linkedin_saved_jobs_url
    log.info(
        "Abriendo Chromium (visible) con perfil %s. Iniciá sesión en LinkedIn "
        "y, cuando termines, CERRÁ la ventana del navegador (Cmd+Q) para que "
        "las cookies se persistan.", profile_path,
    )
    log.info("URL de arranque: %s", target_url)
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                args=["--start-maximized"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(target_url)
            except Exception:
                # even if goto fails, keep the window open so the user can
                # navigate manually.
                pass
            # Wait until the user closes ALL browser windows. persistent_context
            # resolves its `close` event when the last page is gone.
            try:
                ctx.wait_for_event("close", timeout=0)
            except Exception:
                pass
            try:
                ctx.close()
            except Exception:
                pass
    except Exception as e:
        log.error("login helper failed: %s", e)
        return 1
    log.info(
        "Sesión persistida en %s. Ahora corré `python run.py all` — será "
        "headless y usará las cookies recién guardadas.", profile_path,
    )
    return 0


# --------------------------------------------------------------------------- #
# Argparse plumbing                                                           #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-tailored-cv",
        description="Tailor your CV to each LinkedIn saved job, naturally.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        # Positional URL (alias for --job). nargs='?' so it's optional: the
        # command works just as well with `--job <url>` only.
        p.add_argument("job_url", nargs="?", default=None,
                       help="optional job URL (alias for --job; mutually handled)")
        p.add_argument("--dry-run", action="store_true",
                       help="show what would be processed without calling the LLM")
        p.add_argument("--new", action="store_true",
                       help="only process jobs not yet processed")
        p.add_argument("--force", action="store_true",
                       help="re-process even if already processed")
        p.add_argument("--yes", action="store_true",
                       help="skip the bulk-confirm prompt when >1 job targeted")
        p.add_argument("--job", metavar="URL", help="process only this specific job URL")
        p.add_argument("--limit", type=int, default=0, help="process at most N jobs (0=all)")
        p.add_argument("--last", type=int, default=0,
                       help="process only the N most recently saved jobs "
                            "(0=all); sorts by saved_at_iso desc, falling back "
                            "to the saved-jobs listing order")
        p.add_argument("--scraper", default=settings.scraper_backend,
                       choices=("playwright", "browsermcp"),
                       help="scraper backend: 'playwright' (default) or 'browsermcp' (legacy)")
        p.add_argument("--legacy-docx", action="store_true",
                       help="render via the legacy docx + LibreOffice path instead of HTML+Playwright")

    p_all = sub.add_parser("all", help="extract + tailor + render")
    add_common(p_all)
    p_all.set_defaults(func=cmd_all)

    p_extract = sub.add_parser("extract", help="scrape LinkedIn saved jobs only")
    p_extract.add_argument("job_url", nargs="?", default=None,
                           help="optional job URL (alias for --job)")
    p_extract.add_argument("--job", metavar="URL", help="scrape only this job URL (for testing)")
    p_extract.add_argument("--scraper", default=settings.scraper_backend,
                          choices=("playwright", "browsermcp"),
                          help="scraper backend (default: playwright)")
    p_extract.set_defaults(func=cmd_extract)

    p_tailor = sub.add_parser("tailor", help="tailor already-extracted jobs only")
    add_common(p_tailor)
    p_tailor.set_defaults(func=cmd_tailor)

    p_manual = sub.add_parser(
        "manual",
        help="tailor a CV from a pasted (non-LinkedIn) job offer",
    )
    p_manual.add_argument("--title", default=None, help="job title (optional; derived from text if omitted)")
    p_manual.add_argument("--company", default=None, help="company name (optional; 'empresa' if omitted)")
    p_manual.add_argument("--location", default=None, help="job location (optional)")
    p_manual.add_argument("--description", default=None, help="raw job description text (inline)")
    p_manual.add_argument("--description-file", metavar="PATH", default=None,
                          help="path to a .txt file with the job description")
    p_manual.add_argument("--dry-run", action="store_true",
                          help="show what would be processed without calling the LLM")
    p_manual.add_argument("--force", action="store_true",
                          help="re-process even if already processed")
    p_manual.add_argument("--legacy-docx", action="store_true",
                          help="render via the legacy docx + LibreOffice path")
    p_manual.set_defaults(func=cmd_manual)

    p_review = sub.add_parser("review", help="serve cv.html for a job and edit it in place")
    p_review.add_argument("job_slug", help="folder name under output/ (the job slug)")
    p_review.add_argument("--host", default=None, help=f"bind host (default: {settings.review_host})")
    p_review.add_argument("--port", type=int, default=None,
                         help=f"bind port (default: {settings.review_port})")
    p_review.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    p_review.set_defaults(func=cmd_review)

    p_login = sub.add_parser(
        "login",
        help="open Chromium headed so you can log into LinkedIn; cookies persist into .playwright-profile/",
    )
    p_login.add_argument("--url", default=None,
                         help="URL to open (default: your LinkedIn saved-jobs page)")
    p_login.set_defaults(func=cmd_login)

    p_list = sub.add_parser(
        "list",
        help="list available job slugs under output/ (for use with `review`)",
    )
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(settings.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())