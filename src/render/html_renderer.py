"""Render a tailored analysis.json CV into `cv.html` via Jinja2.

The renderer uses `templates/cv_template.html` and `templates/cv_style.css`.
It is fully generic over the section `type` (entry_block / simple_list /
text_block) — section names are just titles, so any combination/order of
sections renders consistently in the same Harvard style.

Hyperlinks (`links`) are emitted from the protected `links` arrays of
`analysis.json`; URLs never reach the LLM, so they survive the tailor pass and
the renderer writes them straight from the base structure.

Each rewritable text node carries `contenteditable="true"` and a unique
`data-field` attribute. The review step (src/review/server.py) lets the user
edit those in place; on save the whole outerHTML is sent back and the PDF is
re-rendered.

Output: `output/<job_slug>/cv.html`. The CSS file is copied next to it so the
page is self-contained when opened from disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config import settings
from src.utils.logging import get_logger

log = get_logger(__name__)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(settings.templates_dir)),
        autoescape=select_autoescape(disabled_extensions=("html",), default=False),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["entry_links_html"] = _build_entry_links_html
    env.filters["skill_item_parts"] = _split_skill_item
    return env


def _build_entry_links_html(value: Any) -> str:
    """Jinja2 filter: given an entry dict with `links`, return the inline HTML
    for its links paragraph (without the surrounding `<p>` tag — the template
    adds that).

    Each protected link renders as `<a href="url">label</a>`; links are joined
    with " · ". Works for ANY entry_block section, not only "proyectos".
    """
    if not isinstance(value, dict):
        return ""
    links = value.get("links") or []
    parts: list[str] = []
    for link in links:
        label = (link.get("label") or "").strip()
        url = (link.get("url") or "").strip()
        if not label:
            continue
        if url:
            parts.append(f'<a href="{url}" target="_blank">{label}</a>')
        else:
            parts.append(label)
    return ' <span class="sep">·</span> '.join(parts)


def _split_skill_item(value: Any) -> dict[str, str]:
    """Split a skill row at its first colon for the two-column layout.

    The YAML contract intentionally keeps simple-list items as plain text, so
    this presentation detail does not constrain other CVs or the LLM output.
    Rows without a colon remain entirely in the values column.
    """
    text = str(value or "").strip()
    label, separator, details = text.partition(":")
    if not separator:
        return {"label": "", "details": text}
    return {"label": label.strip(), "details": details.strip()}


def _build_contact_html(personal_info: dict[str, Any]) -> str:
    """Build the header contact line HTML from `personal_info`:
    `phone | email | location | <a href>label</a> | ...`."""
    parts: list[str] = []
    for key in ("phone", "email", "location"):
        value = (personal_info.get(key) or "").strip()
        if value:
            parts.append(value)
    for link in personal_info.get("links") or []:
        label = (link.get("label") or "").strip()
        url = (link.get("url") or "").strip()
        if not label:
            continue
        if url:
            parts.append(f'<a href="{url}" target="_blank">{label}</a>')
        else:
            parts.append(label)
    return " | ".join(parts)


def _coerce_sections(profile_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize an analysis.json / CVProfile dict into the shape the Jinja
    template expects. Mutates the dict in place; returns it.

    - Ensures `personal_info` exists and derives `contact_html`.
    - Ensures every section has the generic `type`-based fields (`entries`,
      `items`, `text`) with sane defaults.
    """
    profile_data.setdefault("personal_info", {})
    personal_info = profile_data["personal_info"]
    if not isinstance(personal_info, dict):
        personal_info = {}
        profile_data["personal_info"] = personal_info
    profile_data["contact_html"] = _build_contact_html(personal_info)

    sections = profile_data.get("sections", []) or []
    for s in sections:
        if not isinstance(s, dict):
            continue
        for entry in s.get("entries", []) or []:
            if isinstance(entry, dict):
                entry.setdefault("subheading", "")
                entry.setdefault("location", "")
                entry.setdefault("dates", "")
                entry.setdefault("links", [])
                entry.setdefault("bullets", [])
        s.setdefault("entries", [])
        s.setdefault("items", [])
        s.setdefault("text", "")
        s.setdefault("type", "")
    return profile_data


def render(
    analyzed: dict[str, Any],
    out_dir: Path,
    *,
    editable: bool = True,
) -> Path:
    """Render `analyzed` (an analysis.json / CVProfile dict) to `cv.html`.

    Returns the path to the produced HTML. The CSS file
    (`templates/cv_style.css`) is copied next to it so the page is self-contained
    when opened from disk.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Don't mutate the caller's dict.
    payload = dict(analyzed)
    payload = _coerce_sections(payload)

    env = _env()
    template = env.get_template("cv_template.html")
    html = template.render(**payload)

    html_path = out_dir / "cv.html"
    html_path.write_text(html, encoding="utf-8")

    # Copy the shared CSS next to the rendered HTML so Playwright can pick it up
    # via `file://` without depending on a path outside the output dir.
    src_css = settings.templates_dir / "cv_style.css"
    if src_css.exists():
        shutil.copy2(src_css, out_dir / "cv_style.css")

    log.info("html written: %s", html_path)
    return html_path


def render_from_file(
    analysis_json_path: Path,
    base_cv_path: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Convenience wrapper: read analysis.json + base_cv.yaml metadata, merge,
    and render. Used by the `review` subcommand when only the analysis.json is
    available (the base CV provides `personal_info` for the header).
    """
    import json

    analysis_path = Path(analysis_json_path)
    with analysis_path.open("r", encoding="utf-8") as f:
        analysis = json.load(f)

    # If personal_info is missing in analysis.json, supplement from base CV.
    base_path = base_cv_path or settings.base_cv_path
    if base_path.exists():
        from src.profile.cv_reader import read_cv
        base_profile = read_cv(base_path)
        analysis.setdefault("personal_info", base_profile.personal_info.to_dict())
        analyses_sections = analysis.get("sections", []) or []
        # Make sure section `type` is set (LLM should include it but be defensive).
        base_by_title = {s.title: s.type for s in base_profile.sections}
        for s in analyses_sections:
            s.setdefault("type", base_by_title.get(s.get("title", ""), ""))

    return render(analysis, out_dir or analysis_path.parent)
