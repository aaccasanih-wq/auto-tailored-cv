"""Read and structure a base CV in HTML format (BeautifulSoup4 parser).

The base CV (`input/base_cv.html`) is plain text + hyperlinks — no images.
The reader produces a structured `CVProfile` that distinguishes explicitly:

  * rewritable text   — tagline/summary and per-entry bullets
  * immutable fields   — name, contact line, dates, project titles, skill labels
  * protected URLs     — every `<a href="...">` is captured as a structured
                          `{texto, url}` object; URLs are NEVER concatenated
                          into the visible text and they never pass through the
                          LLM (the tailor prompt hides them entirely).

Section `kind` is detected from the DOM (see `_detect_kind`) and falls into one
of: `educacion`, `experiencia`, `proyectos`, `habilidades`. Each kind dictates
the shape of its entries (educación/experiencia/proyectos) or a `table` grid
(habilidades).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

# --------------------------------------------------------------------------- #
# Data types                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class Enlace:
    """A protected hyperlink. URLs in this object never reach the LLM."""
    texto: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"texto": self.texto, "url": self.url}


@dataclass
class CVEntry:
    """A single experience / education / project block of the CV."""
    # Immutable: project role / institution+role / project title.
    titulo: str = ""
    # Immutable: date range shown next to the title.
    fecha: str = ""
    # Immutable subtitle paragraph (used by the Educación entry, optional).
    subtitulo: str = ""
    # Immutable parenthetical descriptor for projects, e.g.
    # "(Dashboard)" or "(Landing Page · Dashboard)". URLs are stripped from
    # the visible text and stored in `enlaces` instead.
    descriptor: str = ""
    # Protected (never modified by the LLM): the structured links of this entry.
    enlaces: list[Enlace] = field(default_factory=list)
    # Rewritable by the LLM.
    bullets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "titulo": self.titulo,
            "fecha": self.fecha,
            "subtitulo": self.subtitulo,
            "descriptor": self.descriptor,
            "enlaces": [e.to_dict() for e in self.enlaces],
            "bullets": list(self.bullets),
        }


@dataclass
class CVSection:
    """A section of the CV.

    - kind "habilidades" populates `.table` (list of `[label, value]` rows).
    - kinds "educacion" / "experiencia" / "proyectos" populate `.entries`.
    """
    title: str
    kind: str = ""
    entries: list[CVEntry] = field(default_factory=list)
    table: list[list[str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.entries and not self.table

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "kind": self.kind,
            "entries": [e.to_dict() for e in self.entries],
            "table": [list(row) for row in self.table],
        }


@dataclass
class CVProfile:
    """Structured representation of a base CV."""
    name: str
    contact: str                     # visible text only (URLs stripped)
    contact_enlaces: list[Enlace]    # links in the contact line (protected)
    summary: str                     # tagline — rewritable
    sections: list[CVSection]
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "contact": self.contact,
            "contact_enlaces": [e.to_dict() for e in self.contact_enlaces],
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
            "raw_text": self.raw_text,
        }

    def to_json(self, path: Path | None = None) -> str:
        data = self.to_dict()
        if path:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps(data, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Parsing                                                                     #
# --------------------------------------------------------------------------- #


_WHITESPACE_RE = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    """Collapse whitespace and trim. None-safe."""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _extract_enlaces(tag: Tag) -> list[Enlace]:
    """Return the ordered `<a>` hyperlinks contained in `tag`.

    Each `<a href>` becomes `{texto, url}`. The visible text returned elsewhere
    does NOT include the URLs themselves.
    """
    enlaces: list[Enlace] = []
    for a in tag.find_all("a"):
        href = a.get("href", "").strip()
        texto = _clean(a.get_text())
        if href or texto:
            enlaces.append(Enlace(texto=texto, url=href))
    return enlaces


def _strip_link_text(tag: Tag) -> str:
    """Return `_clean(tag.get_text())` but with URL substrings removed (they
    were already extracted into enlaces). In practice the visible text of the
    `<a>` already contains only the link text (e.g. "Dashboard"), not the URL,
    so this is just `tag.get_text()` cleaned up.
    """
    return _clean(tag.get_text(separator=" "))


def _parse_bullets(parent: Tag) -> list[str]:
    bullets: list[str] = []
    ul = parent.find("ul", class_="bullets")
    if ul is None:
        return bullets
    for li in ul.find_all("li", recursive=False):
        text = _clean(li.get_text(separator=" "))
        if text:
            bullets.append(text)
    return bullets


_PAREN_SPACES_RE = re.compile(r"\(\s+|\s+\)")


def _normalize_paren(text: str) -> str:
    """Collapse '( Dashboard )' / '( Landing Page · Dashboard )' into
    '(Dashboard)' / '(Landing Page · Dashboard)' so the descriptor exactly
    matches the original docx-era parenthetical form."""
    return _PAREN_SPACES_RE.sub(lambda m: "(" if m.group().startswith("(") else ")", text)


def _parse_project_links(paragraph: Tag) -> tuple[list[Enlace], str]:
    """Parse a `.project-links` `<p>`.

    Returns `(enlaces, descriptor)` where `descriptor` is the visible text with
    the URLs stripped (e.g. "(Dashboard)" or "(Landing Page · Dashboard)").
    The descriptor is the visible, immutable parenthetical shown above the
    bullets in the rendered CV — it explicitly does NOT contain the URLs.
    """
    enlaces = _extract_enlaces(paragraph)
    # The visible text of `.project-links` is exactly "(Dashboard)" or
    # "(Landing Page · Dashboard)" — i.e. the parenthetical form, with the link
    # text substituted for the URL. BeautifulSoup's get_text already yields the
    # link text (not the href), so the descriptor IS the cleaned visible text.
    descriptor = _normalize_paren(_clean(paragraph.get_text(separator=" ")))
    return enlaces, descriptor


def _parse_entry_block(block: Tag, kind: str) -> CVEntry:
    entry = CVEntry()
    # Header row (title + date): look inside `.entry-row` or `.project-header`.
    header = block.find(class_="entry-row") or block.find(class_="project-header")
    if header is not None:
        title_tag = header.find(class_="entry-title") or header.find(class_="project-title")
        date_tag = header.find(class_="entry-date")
        if title_tag is not None:
            entry.titulo = _clean(title_tag.get_text(separator=" "))
        if date_tag is not None:
            entry.fecha = _clean(date_tag.get_text(separator=" "))
    # Optional subtitle (Educación entry-subtitle).
    subtitle_tag = block.find(class_="entry-subtitle")
    if subtitle_tag is not None:
        entry.subtitulo = _clean(subtitle_tag.get_text(separator=" "))
    # Optional project-links paragraph.
    pl_tag = block.find(class_="project-links")
    if pl_tag is not None:
        entry.enlaces, entry.descriptor = _parse_project_links(pl_tag)
    # Bullets.
    entry.bullets = _parse_bullets(block)
    return entry


def _parse_skills_table(section: Tag) -> list[list[str]]:
    table: list[list[str]] = []
    html_table = section.find("table", class_="skills-table")
    if html_table is None:
        return table
    for tr in html_table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        row = [_clean(c.get_text(separator=" ")) for c in cells]
        if any(row):
            table.append(row)
    return table


def _detect_kind(section: Tag) -> str:
    if section.find(class_="project-block") is not None:
        return "proyectos"
    if section.find(class_="entry-block") is not None:
        return "experiencia"
    if section.find("table", class_="skills-table") is not None:
        return "habilidades"
    # Fall back: has entry-row but no entry-block → educación layout.
    if section.find(class_="entry-row") is not None:
        return "educacion"
    return ""


def _parse_section(section: Tag) -> CVSection:
    title_tag = section.find(class_="section-title")
    title = _clean(title_tag.get_text(separator=" ")) if title_tag else ""
    kind = _detect_kind(section)
    cv_section = CVSection(title=title, kind=kind)

    if kind == "habilidades":
        cv_section.table = _parse_skills_table(section)
    elif kind in {"educacion", "experiencia", "proyectos"}:
        blocks: list[Tag] = []
        if kind == "proyectos":
            blocks = section.find_all(class_="project-block", recursive=False)
            if not blocks:
                blocks = section.find_all(class_="project-block")
        elif kind == "experiencia":
            blocks = section.find_all(class_="entry-block", recursive=False)
            if not blocks:
                blocks = section.find_all(class_="entry-block")
        else:  # educación: no entry-block wrapper, the section itself is the entry.
            if section.find(class_="entry-row") is not None:
                blocks = [section]
            else:
                blocks = []
        for block in blocks:
            cv_section.entries.append(_parse_entry_block(block, kind))
    return cv_section


def _parse_header(header: Tag) -> tuple[str, str, list[Enlace], str]:
    """Returns (name, contact_visible_text, contact_enlaces, summary)."""
    name_tag = header.find(class_="name")
    name = _clean(name_tag.get_text(separator=" ")) if name_tag else ""
    contact_tag = header.find(class_="contact-line")
    contact = ""
    contact_enlaces: list[Enlace] = []
    if contact_tag is not None:
        contact_enlaces = _extract_enlaces(contact_tag)
        contact = _strip_link_text(contact_tag)
    summary_tag = header.find(class_="tagline")
    summary = _clean(summary_tag.get_text(separator=" ")) if summary_tag else ""
    return name, contact, contact_enlaces, summary


def read_cv(path: Path) -> CVProfile:
    """Read an HTML base CV and return a structured CVProfile."""
    if not Path(path).exists():
        raise FileNotFoundError(f"base CV not found at {path}")
    html = Path(path).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    header = soup.find(class_="header")
    name = ""
    contact = ""
    contact_enlaces: list[Enlace] = []
    summary = ""
    if header is not None:
        name, contact, contact_enlaces, summary = _parse_header(header)

    sections: list[CVSection] = []
    for section_tag in soup.find_all(class_="section"):
        cv_section = _parse_section(section_tag)
        if not cv_section.is_empty():
            sections.append(cv_section)

    raw_text = _render_text(name, contact, summary, sections)
    return CVProfile(
        name=name,
        contact=contact,
        contact_enlaces=contact_enlaces,
        summary=summary,
        sections=sections,
        raw_text=raw_text,
    )


def _render_text(name: str, contact: str, summary: str, sections: list[CVSection]) -> str:
    """Build a clean plain-text representation of the CV for LLM context."""
    parts: list[str] = []
    if name:
        parts.append(name)
    if contact:
        parts.append(contact)
    if summary:
        parts.append("")
        parts.append(summary)
    for section in sections:
        parts.append("")
        parts.append(section.title)
        parts.append("=" * len(section.title))
        if section.kind == "habilidades":
            for row in section.table:
                parts.append(" | ".join(cell for cell in row if cell))
        else:
            for entry in section.entries:
                if entry.titulo or entry.fecha:
                    parts.append(f"- {entry.titulo} | {entry.fecha}".rstrip(" |"))
                if entry.subtitulo:
                    parts.append(entry.subtitulo)
                if entry.descriptor:
                    parts.append(entry.descriptor)
                for bullet in entry.bullets:
                    parts.append(f"  · {bullet}")
    return "\n".join(parts).strip() + "\n"


__all__ = [
    "CVProfile",
    "CVSection",
    "CVEntry",
    "Enlace",
    "read_cv",
]