"""Render an `analysis.json` tailored CV into `cv.html` via Jinja2.

The renderer uses `templates/cv_template.html` and reuses `templates/cv_style.css`.
The HTML it produces is identical in styling to `input/base_cv.html` — same CSS
file, same `.entry-block` / `.project-block` primitives — so the tailored CV's
look-and-feel matches the base CV.

Hyperlinks (`enlaces`) are emitted from the protected `enlaces` arrays of
`analysis.json`; URLs never reach the LLM, so they survive the tailor pass and
the renderer writes them straight from the base structure.

Each rewritable text node carries `contenteditable="true"` and a unique
`data-field` attribute (e.g. `summary`, `section.2.entry.0.bullet.3`,
`section.3.entry.1.subtitulo`). The review step (src/review/server.py) lets the
user edit those in place; on save, the entire `outerHTML` of the page is sent to
the server, which re-renders the PDF.

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
    env.filters["project_links_html"] = _build_project_links_html
    return env


def _build_project_links_html(value: Any) -> str:
    """Jinja2 filter: given an entry dict with `descriptor` + `enlaces`,
    return the inline HTML for the project-links paragraph (without the
    surrounding `<p>` tag — the template adds that).

    Rules:
    - If there's a non-empty descriptor, render it inside parens. For each
      enlace whose `texto` is a substring of the descriptor, replace the
      first occurrence with `<a href="url">texto</a>`. Any enlace whose
      texto is NOT in the descriptor is appended after a " · " separator.
    - If there's no descriptor but there are enlaces, render the enlaces
      as `tex · text · ` joins inside parens, each clickable.
    - If neither: return empty string.
    """
    if not isinstance(value, dict):
        return ""
    descriptor = (value.get("descriptor") or "").strip()
    enlaces = value.get("enlaces") or []
    # Strip surrounding parens from descriptor; we add them ourselves.
    if descriptor.startswith("(") and descriptor.endswith(")"):
        descriptor = descriptor[1:-1].strip()
    if not descriptor and not enlaces:
        return ""
    parts: list[str] = []
    if descriptor:
        for enlace in enlaces:
            texto = (enlace.get("texto") or "").strip()
            url = (enlace.get("url") or "").strip()
            if not texto or not url:
                continue
            if texto in descriptor:
                anchor = f'<a href="{url}" target="_blank">{texto}</a>'
                descriptor = descriptor.replace(texto, anchor, 1)
            else:
                parts.append(f'<a href="{url}" target="_blank">{texto}</a>')
        rendered = descriptor
        if parts:
            rendered += ' <span class="sep">·</span> ' + ' <span class="sep">·</span> '.join(parts)
        return f"({rendered})"
    else:
        links = []
        for enlace in enlaces:
            texto = (enlace.get("texto") or "").strip()
            url = (enlace.get("url") or "").strip()
            if not texto:
                continue
            if url:
                links.append(f'<a href="{url}" target="_blank">{texto}</a>')
            else:
                links.append(texto)
        if not links:
            return ""
        return "(" + ' <span class="sep">·</span> '.join(links) + ")"


def _build_contact_html(
    contact_text: str,
    contact_enlaces: list[dict[str, str]],
) -> str:
    """Reconstruct the contact-line HTML by substituting each placeholder for
    `<a href>`. The base CV's contact line has the form:
        "PHONE | EMAIL | Sitio web | Mis Proyectos | DNI | City"
    where the link text labels ("Sitio web", "Mis Proyectos") are placeholders
    substituted with `<a>`. We replace them in order.
    """
    text = contact_text
    for enlace in contact_enlaces:
        label = enlace.get("texto", "")
        url = enlace.get("url", "")
        if not label or not url:
            continue
        anchor = f'<a href="{url}" target="_blank">{label}</a>'
        text = text.replace(label, anchor, 1)
    return text


def _coerce_sections(profile_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize an analysis.json / CVProfile dict into the shape the Jinja
    template expects. Mutates the dict in place; returns it.

    - Sections always have `kind`.
    - If the input has `contact_enlaces`, build `contact_html` and drop it.
    - `enlaces` per entry is preserved as-is (used by the renderer).
    """
    contact_enlaces = profile_data.pop("contact_enlaces", None) or []
    contact_html = _build_contact_html(
        profile_data.get("contact", ""),
        contact_enlaces,
    )
    profile_data["contact_html"] = contact_html
    # Sections: ensure 'enlaces' arrays are present (default empty list).
    sections = profile_data.get("sections", []) or []
    for s in sections:
        for entry in s.get("entries", []) or []:
            entry.setdefault("enlaces", [])
            entry.setdefault("subtitulo", "")
            entry.setdefault("descriptor", "")
            entry.setdefault("bullets", [])
        s.setdefault("table", [])
        s.setdefault("entries", [])
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
    """Convenience wrapper: read analysis.json + base_cv.html metadata,
    merge, and render. Used by the `review` subcommand when only the
    analysis.json is available (the base CV provides `name`, `contact`,
    and `contact_enlaces` for the header).
    """
    import json

    analysis_path = Path(analysis_json_path)
    with analysis_path.open("r", encoding="utf-8") as f:
        analysis = json.load(f)

    # If name/contact are missing in analysis.json, supplement from base CV.
    base_path = base_cv_path or settings.base_cv_path
    if base_path.exists():
        from src.profile.cv_reader import read_cv
        base_profile = read_cv(base_path)
        analysis.setdefault("name", base_profile.name)
        analysis.setdefault("contact", base_profile.contact)
        analysis.setdefault("contact_enlaces", [e.to_dict() for e in base_profile.contact_enlaces])
        analyses_sections = analysis.get("sections", []) or []
        # Make sure section kind is set (LLM should include it but be defensive).
        base_by_title = {s.title: s.kind for s in base_profile.sections}
        for s in analyses_sections:
            s.setdefault("kind", base_by_title.get(s.get("title", ""), ""))

    return render(analysis, out_dir or analysis_path.parent)