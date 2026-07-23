"""Slugify helpers for building safe, readable filesystem names."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MULTIPLE_DASH_RE = re.compile(r"-{2,}")


def slugify(text: str, max_length: int = 60) -> str:
    """Normalize a free text string into a filesystem-safe slug.

    Lowercase, ASCII-only, words separated by single dashes.
    Examples:
        "Senior Data Engineer (Remote)" -> "senior-data-engineer-remote"
        "Ingeniero de Datos Sénior"     -> "ingeniero-de-datos-senior"
    """
    if not text:
        return "untitled"
    # NFKD: split accents, then drop non-ASCII
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    slug = _SLUG_RE.sub("-", lowered).strip("-")
    slug = _MULTIPLE_DASH_RE.sub("-", slug)
    if not slug:
        slug = "untitled"
    return slug[:max_length]


def job_folder_name(title: str, company: str, when: date | None = None) -> str:
    """Build the job folder name (without date) for a tailored CV.

    Format: <title-slug>_<company-slug>
    Example: senior-data-engineer_acme

    The date is handled separately by :func:`job_output_path`, which nests
    the job folder under a date directory: output/<YYYY-MM-DD>/<job_folder>.
    """
    title_slug = slugify(title, max_length=50)
    company_slug = slugify(company, max_length=30)
    return f"{title_slug}_{company_slug}"


def job_output_path(
    output_dir: Path,
    title: str,
    company: str,
    when: date | None = None,
) -> Path:
    """Resolve the full output path for a tailored CV.

    Layout: ``<output_dir>/<YYYY-MM-DD>/<title-slug>_<company-slug>``
    Example: ``output/2026-07-13/senior-data-engineer_acme``
    """
    when = when or date.today()
    return Path(output_dir) / when.isoformat() / job_folder_name(title, company)


__all__ = ["slugify", "job_folder_name", "job_output_path"]