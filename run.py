"""CLI entrypoint for auto-tailored-cv.

Usage:
    python run.py all                 # extract + tailor + render
    python run.py extract             # only scrape LinkedIn into jobs/
    python run.py tailor              # only tailor already-extracted jobs
    python run.py all --new           # only process jobs saved since last run
    python run.py all --job <url>     # process one specific job URL
    python run.py all --dry-run       # show what would be processed, no LLM calls
    python run.py all --force         # re-process already-processed jobs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import List, Optional

from src.config import ensure_dirs, settings
from src.profile.cv_reader import read_cv
from src.tailor.evaluator import EvaluationResult, evaluate, save_evaluation_json
from src.tailor.evaluator import parse_evaluation
from src.tailor.llm_client import LLMResponse, make_client
from src.tailor.prompts import JobInfo
from src.tailor.cv_rewriter import TailorResult, save_tailored_json, tailor_cv
from src.tailor.repair import RepairResult, repair_cv, save_repaired_json
from src.render.docx_writer import write_tailored_docx
from src.render.pdf_converter import convert_docx_to_pdf
from src.extract.linkedin_scraper import (
    SavedJob,
    extract_saved_jobs,
    save_jobs_json,
)
from src.utils.logging import configure_logging, get_logger
from src.utils.slugify import job_folder_name

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Cache helpers                                                               #
# --------------------------------------------------------------------------- #


def _job_cache_path(job: SavedJob) -> Path:
    key = (job.job_id or job.url).rstrip("/").split("/")[-1] or "untitled"
    # Filesystem-safe key, stripped of any query params
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


def _load_cached_jobs() -> List[SavedJob]:
    jobs: List[SavedJob] = []
    if not settings.jobs_dir.exists():
        return jobs
    for f in sorted(settings.jobs_dir.glob("*.json")):
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


def do_extract(target_url: Optional[str] = None) -> List[SavedJob]:
    """Run the LinkedIn scrape and cache results."""
    log.info("extracting saved jobs from LinkedIn via BrowserMCP…")
    try:
        jobs = asyncio.run(extract_saved_jobs(saved_jobs_url=target_url or ""))
    except Exception as e:
        log.error("extraction failed: %s", e)
        return []
    if not jobs:
        log.warning("no saved jobs were extracted")
        return []
    # Cache each job file keyed by job_id/url.
    for job in jobs:
        _save_job_cache(job, tailored=False)
    # Also write a consolidated snapshot for human inspection.
    save_jobs_json(jobs, settings.jobs_dir / "_all_saved_jobs.json")
    log.info("extracted %d saved jobs", len(jobs))
    return jobs


def _render_job(job: SavedJob, tailored_json: dict, out_dir: Path) -> Path:
    """Render the tailored JSON to .docx + .pdf in out_dir. Returns the docx path."""
    docx_path = out_dir / "cv.docx"
    write_tailored_docx(settings.base_cv_path, tailored_json, docx_path)
    # PDF conversion is best-effort: if LibreOffice isn't installed yet, the
    # DOCX is still useful — we just log the conversion failure and move on.
    pdf_result = convert_docx_to_pdf(docx_path, out_dir)
    if not pdf_result.success:
        log.warning("pdf conversion failed: %s", pdf_result.error)
    return docx_path


def _tailor_one(
    client,
    base_profile,
    job: SavedJob,
    dry_run: bool,
) -> Optional[Path]:
    """Run tailor + evaluate + repair + render for a single job. Returns the
    output folder path, or None on failure."""
    job_info = JobInfo(
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
    )

    folder_name = job_folder_name(job.title, job.company or "company")
    out_dir = settings.output_dir / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Persist the raw job description for traceability.
    (out_dir / "job_description.txt").write_text(job.description, encoding="utf-8")

    if dry_run:
        log.info("[dry-run] would tailor for '%s' at %s -> %s", job.title, job.company, out_dir)
        return out_dir

    # 1) Tailor
    log.info("tailoring CV for '%s' at %s", job.title, job.company)
    tailored = tailor_cv(client, base_profile, job_info)
    save_tailored_json(tailored, out_dir / "analysis.json")
    if tailored.tailored_json is None or not tailored.tailored_json.get("sections"):
        log.error("tailor produced empty/null sections for %s; skipping", job.title)
        return None

    # 2) Evaluate
    log.info("evaluating tailored CV for '%s'", job.title)
    evaluation = evaluate(client, base_profile, job_info, tailored.tailored_json)

    # 3) Conditional repair
    final_json = tailored.tailored_json
    if evaluation.needs_repair and evaluation.issues:
        log.info("repairing %d issue(s) for '%s'", len(evaluation.issues), job.title)
        repaired = repair_cv(client, base_profile, tailored.tailored_json, evaluation.issues)
        if repaired.repaired_json:
            final_json = repaired.repaired_json
            save_repaired_json(repaired, out_dir / "analysis_repaired.json")
    save_evaluation_json(evaluation, out_dir / "evaluation.json")

    # 4) Render
    log.info("rendering .docx/.pdf for '%s'", job.title)
    _render_job(job, final_json, out_dir)

    _save_job_cache(job, tailored=True)
    log.info("done: %s", out_dir)
    return out_dir


def do_tailor(jobs: List[SavedJob], dry_run: bool = False, force: bool = False) -> List[Path]:
    if not jobs:
        log.warning("no jobs to tailor")
        return []
    base_profile = read_cv(settings.base_cv_path)
    log.info("base CV: %d sections", len(base_profile.sections))
    if dry_run:
        # No client needed
        client = None  # type: ignore[assignment]
    else:
        if not settings.is_configured:
            log.error("OPENCODE_API_KEY is not set. Copy .env.example to .env and paste your OpenCode API key.")
            return []
        client = make_client()

    outputs: List[Path] = []
    for i, job in enumerate(jobs, start=1):
        log.info("--- [%d/%d] %s @ %s ---", i, len(jobs), job.title, job.company)
        if not force and _is_processed(job):
            log.info("already processed — skipping (use --force to redo).")
            continue
        try:
            out = _tailor_one(client, base_profile, job, dry_run=dry_run)
            if out is not None:
                outputs.append(out)
        except Exception as e:
            log.error("tailoring failed for %s: %s", job.url, e)
    return outputs


# --------------------------------------------------------------------------- #
# Commands                                                                    #
# --------------------------------------------------------------------------- #


def cmd_extract(args: argparse.Namespace) -> int:
    ensure_dirs()
    target = args.job  # may be None → default saved-jobs page
    jobs = do_extract(target_url=target)
    if not jobs:
        return 1
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    ensure_dirs()
    # jobs come from the cache (jobs/*.json), so the user can run
    # `tailor` without re-scraping.
    jobs = _load_cached_jobs()
    if args.job:
        # Filter cached jobs to just the requested URL
        jobs = [j for j in jobs if (args.job in j.url or j.url in args.job)]
        if not jobs:
            log.error("no cached job matches --job %s", args.job)
            return 2
    if args.new:
        jobs = [j for j in jobs if not _is_processed(j)]
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]
    outputs = do_tailor(jobs, dry_run=args.dry_run, force=args.force)
    log.info("produced %d tailored CV(s)", len(outputs))
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    ensure_dirs()
    jobs = do_extract(target_url=args.job)
    if args.new:
        jobs = [j for j in jobs if not _is_processed(j)]
    outputs = do_tailor(jobs, dry_run=args.dry_run, force=args.force)
    log.info("produced %d tailored CV(s)", len(outputs))
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
        p.add_argument("--dry-run", action="store_true", help="show what would be processed without calling the LLM")
        p.add_argument("--new", action="store_true", help="only process jobs not yet processed")
        p.add_argument("--force", action="store_true", help="re-process even if already processed")
        p.add_argument("--job", metavar="URL", help="process only this specific job URL")
        p.add_argument("--limit", type=int, default=0, help="process at most N jobs (0=all)")

    p_all = sub.add_parser("all", help="extract + tailor + render")
    add_common(p_all)
    p_all.set_defaults(func=cmd_all)

    p_extract = sub.add_parser("extract", help="scrape LinkedIn saved jobs only")
    p_extract.add_argument("--job", metavar="URL", help="scrape only this job URL (for testing)")
    p_extract.set_defaults(func=cmd_extract)

    p_tailor = sub.add_parser("tailor", help="tailor already-extracted jobs only")
    add_common(p_tailor)
    p_tailor.set_defaults(func=cmd_tailor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(settings.log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())