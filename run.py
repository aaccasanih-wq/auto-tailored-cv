"""CLI entrypoint for auto-tailored-cv.

Usage:
    python run.py all                 # extract + tailor + render (HTML + PDF)
    python run.py extract            # only scrape LinkedIn into jobs/
    python run.py tailor             # only tailor already-extracted jobs
    python run.py review <job_slug>  # editable review of a job's cv.html
    python run.py all --new          # only process jobs saved since last run
    python run.py all --job <url>    # process one specific job URL
    python run.py all --dry-run      # show what would be processed, no LLM calls
    python run.py all --force        # re-process already-processed jobs
    python run.py all --scraper playwright    # default; Playwright MCP
    python run.py all --scraper browsermcp     # legacy fallback; Browser MCP
    python run.py all --legacy-docx   # render via docx_writer + LibreOffice
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.config import ensure_dirs, settings
from src.extract.linkedin_scraper import (
    SavedJob,
    extract_saved_jobs,
    save_jobs_json,
    _normalize_job_url,
)
from src.profile.cv_reader import read_cv
from src.render import html_renderer, pdf_renderer
from src.tailor.cv_rewriter import save_tailored_json, tailor_cv
from src.tailor.evaluator import evaluate, save_evaluation_json
from src.tailor.llm_client import make_client
from src.tailor.prompts import JobInfo
from src.tailor.repair import repair_cv, save_repaired_json
from src.utils.logging import configure_logging, get_logger
from src.utils.slugify import job_output_path

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Cache helpers                                                               #
# --------------------------------------------------------------------------- #


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


def _save_job_cache(job: SavedJob, tailored: bool) -> None:
    cache = _job_cache_path(job)
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload = job.to_dict()
    payload["tailored"] = tailored
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        ))
    return jobs


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
        _save_job_cache(job, tailored=False)
    save_jobs_json(jobs, settings.jobs_dir / "_all_saved_jobs.json")
    log.info("extracted %d saved jobs", len(jobs))
    return jobs


def _resolve_job_folder(job: SavedJob) -> Path:
    return job_output_path(
        settings.output_dir, job.title, job.company or "company",
    )


def _render_job_html_pdf(
    tailored_json: dict,
    out_dir: Path,
) -> tuple[Path | None, Path | None]:
    """Render the tailored JSON to cv.html + cv.pdf via Jinja2 + Playwright."""
    try:
        # Supplement the analysis.json with header (name/contact/contact_enlaces)
        # from the base CV so the rendered page matches base_cv.html's header.
        base_profile = read_cv(settings.base_cv_path)
        payload = dict(tailored_json)
        payload.setdefault("name", base_profile.name)
        payload.setdefault("contact", base_profile.contact)
        payload.setdefault(
            "contact_enlaces",
            [e.to_dict() for e in base_profile.contact_enlaces],
        )
        # Make sure each section has a 'kind' so the Jinja template renders
        # the right blocks. Default-derive from base CV by title match.
        base_by_title = {s.title: s.kind for s in base_profile.sections}
        for s in payload.get("sections", []) or []:
            s.setdefault("kind", base_by_title.get(s.get("title", ""), ""))
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
    """Render via the legacy python-docx + LibreOffice path (behind --legacy-docx)."""
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
) -> Path | None:
    """Run tailor + evaluate + repair + render for a single job. Returns the
    output folder path, or None on failure."""
    job_info = JobInfo(
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
    )

    out_dir = _resolve_job_folder(job)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "job_description.txt").write_text(job.description, encoding="utf-8")

    if dry_run:
        log.info("[dry-run] would tailor for '%s' at %s -> %s", job.title, job.company, out_dir)
        return out_dir

    log.info("tailoring CV for '%s' at %s", job.title, job.company)
    tailored = tailor_cv(client, base_profile, job_info)
    save_tailored_json(tailored, out_dir / "analysis.json")
    if tailored.tailored_json is None or not tailored.tailored_json.get("sections"):
        log.error("tailor produced empty/null sections for %s; skipping", job.title)
        return None

    log.info("evaluating tailored CV for '%s'", job.title)
    evaluation = evaluate(client, base_profile, job_info, tailored.tailored_json)

    final_json = tailored.tailored_json
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
        repaired = repair_cv(client, base_profile, tailored.tailored_json, semantic_issues)
        if repaired.repaired_json:
            final_json = repaired.repaired_json
            save_repaired_json(repaired, out_dir / "analysis_repaired.json")
    save_evaluation_json(evaluation, out_dir / "evaluation.json")

    if legacy_docx:
        log.info("rendering .docx/.pdf (legacy) for '%s'", job.title)
        _render_job_legacy_docx(final_json, out_dir)
    else:
        log.info("rendering cv.html/cv.pdf for '%s'", job.title)
        _render_job_html_pdf(final_json, out_dir)

    _save_job_cache(job, tailored=True)
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
    log.info("base CV (%s): %d sections", settings.base_cv_path, len(base_profile.sections))
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
            log.info("already processed — skipping (use --force to redo).")
            continue
        try:
            out = _tailor_one(
                client, base_profile, job, dry_run=dry_run, legacy_docx=legacy_docx,
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
# Commands                                                                    #
# --------------------------------------------------------------------------- #


def cmd_extract(args: argparse.Namespace) -> int:
    ensure_dirs()
    target = args.job
    jobs = do_extract(target_url=target, scraper_backend=args.scraper)
    if not jobs:
        return 1
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    ensure_dirs()
    jobs = _load_cached_jobs()
    if args.job:
        norm_job = _normalize_job_url(args.job)
        jobs = [j for j in jobs if (norm_job in j.url or j.url in norm_job)]
        if not jobs:
            log.error("no cached job matches --job %s", args.job)
            return 2
    if args.new:
        jobs = [j for j in jobs if not _is_processed(j)]
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]
    outputs = do_tailor(
        jobs, dry_run=args.dry_run, force=args.force, legacy_docx=args.legacy_docx,
    )
    log.info("produced %d tailored CV(s)", len(outputs))
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    ensure_dirs()
    jobs = do_extract(target_url=args.job, scraper_backend=args.scraper)
    if args.new:
        jobs = [j for j in jobs if not _is_processed(j)]
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]
    outputs = do_tailor(
        jobs, dry_run=args.dry_run, force=args.force, legacy_docx=args.legacy_docx,
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
        p.add_argument("--dry-run", action="store_true",
                       help="show what would be processed without calling the LLM")
        p.add_argument("--new", action="store_true",
                       help="only process jobs not yet processed")
        p.add_argument("--force", action="store_true",
                       help="re-process even if already processed")
        p.add_argument("--job", metavar="URL", help="process only this specific job URL")
        p.add_argument("--limit", type=int, default=0, help="process at most N jobs (0=all)")
        p.add_argument("--scraper", default=settings.scraper_backend,
                       choices=("playwright", "browsermcp"),
                       help="scraper backend: 'playwright' (default) or 'browsermcp' (legacy)")
        p.add_argument("--legacy-docx", action="store_true",
                       help="render via the legacy docx + LibreOffice path instead of HTML+Playwright")

    p_all = sub.add_parser("all", help="extract + tailor + render")
    add_common(p_all)
    p_all.set_defaults(func=cmd_all)

    p_extract = sub.add_parser("extract", help="scrape LinkedIn saved jobs only")
    p_extract.add_argument("--job", metavar="URL", help="scrape only this job URL (for testing)")
    p_extract.add_argument("--scraper", default=settings.scraper_backend,
                          choices=("playwright", "browsermcp"),
                          help="scraper backend (default: playwright)")
    p_extract.set_defaults(func=cmd_extract)

    p_tailor = sub.add_parser("tailor", help="tailor already-extracted jobs only")
    add_common(p_tailor)
    p_tailor.set_defaults(func=cmd_tailor)

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