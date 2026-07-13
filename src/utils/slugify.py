"""Slugify helpers for building safe, readable filesystem names."""

from __future__ import annotations

import re
import unicodedata
from datetime import date


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
    """Build the date-prefixed folder name for a tailored CV.

    Format: YYYY-MM-DD_<title-slug>_<company-slug>
    Example: 2026-07-13_senior-data-engineer_acme
    """
    when = when or date.today()
    date_prefix = when.isoformat()
    title_slug = slugify(title, max_length=50)
    company_slug = slugify(company, max_length=30)
    return f"{date_prefix}_{title_slug}_{company_slug}"


__all__ = ["slugify", "job_folder_name"]