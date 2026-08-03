"""Read and structure a base CV in YAML format.

The base CV (`input/base_cv.yaml`) is validated against
`schema/base_cv.schema.json` and produces a structured `CVProfile`:

  * rewritable text      — `summary`, bullet/`item` `text`, `text_block` text
  * immutable fields     — personal info, entry heading/dates/subheading/location
  * protected URLs       — every `links` entry is captured as a structured
                            `{label, url}` object; URLs NEVER reach the LLM
                            (the prompt builders strip them entirely).

Sections are generic: `type` is one of `entry_block`, `simple_list`,
`text_block`. A section whose `type` is unknown, or a file that does not
validate against the schema, fails with an explicit, actionable exception —
never silently discarded (the old HTML reader's `is_empty()` behaviour is gone).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.profile.schema_validation import require_valid_yaml

SECTION_TYPES = ("entry_block", "simple_list", "text_block")


# --------------------------------------------------------------------------- #
# Data types                                                                  #
# --------------------------------------------------------------------------- #


@dataclass
class Enlace:
    """A protected hyperlink. URLs in this object never reach the LLM."""
    label: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "url": self.url}


@dataclass
class CVBullet:
    """A single bullet of an entry. `text` is rewritable; `tags` are optional
    keywords the bullet demonstrates (enable pre-LLM local filtering)."""
    text: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "tags": list(self.tags)}


@dataclass
class CVItem:
    """A single item of a `simple_list` section (skills, languages, awards...)."""
    text: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "tags": list(self.tags)}


@dataclass
class CVEntry:
    """A single entry of an `entry_block` section (experience, education,
    project, certification...)."""
    heading: str = ""
    subheading: str = ""
    location: str = ""
    dates: str = ""
    # Protected (never modified by the LLM): structured links of this entry.
    links: list[Enlace] = field(default_factory=list)
    # Rewritable by the LLM.
    bullets: list[CVBullet] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "subheading": self.subheading,
            "location": self.location,
            "dates": self.dates,
            "links": [e.to_dict() for e in self.links],
            "bullets": [b.to_dict() for b in self.bullets],
        }


@dataclass
class CVSection:
    """A generic CV section.

    - `type` is one of: `entry_block` (uses `entries`), `simple_list` (uses
      `items`), `text_block` (uses `text`).
    - `reorderable` gives the LLM licence to reorder/omit entries for relevance.
    """
    id: str = ""
    title: str = ""
    type: str = ""
    reorderable: bool = False
    entries: list[CVEntry] = field(default_factory=list)
    items: list[CVItem] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "reorderable": self.reorderable,
            "entries": [e.to_dict() for e in self.entries],
            "items": [i.to_dict() for i in self.items],
            "text": self.text,
        }


@dataclass
class PersonalInfo:
    """Personal/contact data from `personal_info`."""
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[Enlace] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "links": [e.to_dict() for e in self.links],
        }


@dataclass
class CVProfile:
    """Structured representation of a base CV."""
    personal_info: PersonalInfo = field(default_factory=PersonalInfo)
    summary: str = ""
    sections: list[CVSection] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "personal_info": self.personal_info.to_dict(),
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
        }

    def to_json(self, path: Path | None = None) -> str:
        data = self.to_dict()
        if path:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps(data, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Parsing                                                                     #
# --------------------------------------------------------------------------- #


def _parse_enlaces(raw: list | None) -> list[Enlace]:
    out: list[Enlace] = []
    for item in raw or []:
        if isinstance(item, dict):
            label = str(item.get("label") or "").strip()
            url = str(item.get("url") or "").strip()
            if label or url:
                out.append(Enlace(label=label, url=url))
    return out


def _parse_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]


def _parse_bullets(raw: Any) -> list[CVBullet]:
    out: list[CVBullet] = []
    for b in raw or []:
        if isinstance(b, str):
            if b.strip():
                out.append(CVBullet(text=b.strip()))
        elif isinstance(b, dict):
            out.append(CVBullet(text=str(b.get("text") or "").strip(), tags=_parse_tags(b.get("tags"))))
    return out


def _parse_items(raw: Any) -> list[CVItem]:
    out: list[CVItem] = []
    for i in raw or []:
        if isinstance(i, str):
            if i.strip():
                out.append(CVItem(text=i.strip()))
        elif isinstance(i, dict):
            out.append(CVItem(text=str(i.get("text") or "").strip(), tags=_parse_tags(i.get("tags"))))
    return out


def _parse_entry(raw: dict) -> CVEntry:
    return CVEntry(
        heading=str(raw.get("heading") or "").strip(),
        subheading=str(raw.get("subheading") or "").strip(),
        location=str(raw.get("location") or "").strip(),
        dates=str(raw.get("dates") or "").strip(),
        links=_parse_enlaces(raw.get("links")),
        bullets=_parse_bullets(raw.get("bullets")),
    )


def _parse_section(raw: dict) -> CVSection:
    type_ = str(raw.get("type") or "").strip()
    if type_ not in SECTION_TYPES:
        raise ValueError(
            f"sección '{raw.get('title') or raw.get('id')}': type '{type_}' no "
            f"reconocido. Debe ser uno de {list(SECTION_TYPES)}."
        )
    return CVSection(
        id=str(raw.get("id") or "").strip(),
        title=str(raw.get("title") or "").strip(),
        type=type_,
        reorderable=bool(raw.get("reorderable", False)),
        entries=[_parse_entry(e) for e in (raw.get("entries") or []) if isinstance(e, dict)],
        items=_parse_items(raw.get("items")),
        text=str(raw.get("text") or "").strip(),
    )


def read_cv(path: Path) -> CVProfile:
    """Read a YAML base CV and return a structured CVProfile.

    Raises `BaseCvValidationError` (with readable, actionable errors) if the
    file does not validate against the schema, and `FileNotFoundError` if the
    path does not exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"base CV not found at {path}")
    # Fails loudly (never silently drops a section) if the file is invalid.
    require_valid_yaml(path)

    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    pi_raw = data.get("personal_info") or {}
    personal_info = PersonalInfo(
        name=str(pi_raw.get("name") or ""),
        email=str(pi_raw.get("email") or ""),
        phone=str(pi_raw.get("phone") or ""),
        location=str(pi_raw.get("location") or ""),
        links=_parse_enlaces(pi_raw.get("links")),
    )
    sections = [
        _parse_section(s)
        for s in (data.get("sections") or [])
        if isinstance(s, dict)
    ]
    profile = CVProfile(
        personal_info=personal_info,
        summary=str(data.get("summary") or "").strip(),
        sections=sections,
    )
    profile.raw_text = _render_text(profile)
    return profile


def _render_text(profile: CVProfile) -> str:
    """Build a clean plain-text representation of the CV for LLM context.
    URLs are NOT included (they are protected)."""
    pi = profile.personal_info
    parts: list[str] = []
    if pi.name:
        parts.append(pi.name)
    contact = " | ".join(x for x in (pi.phone, pi.email, pi.location) if x)
    if contact:
        parts.append(contact)
    if profile.summary:
        parts.append("")
        parts.append(profile.summary)
    for section in profile.sections:
        parts.append("")
        parts.append(section.title)
        parts.append("=" * len(section.title))
        if section.type == "text_block":
            parts.append(section.text)
        elif section.type == "simple_list":
            for item in section.items:
                parts.append(f"- {item.text}")
        else:  # entry_block
            for entry in section.entries:
                head = entry.heading
                if entry.dates:
                    head = f"{head} | {entry.dates}" if head else entry.dates
                if head:
                    parts.append(f"- {head}")
                if entry.subheading:
                    parts.append(entry.subheading)
                if entry.location:
                    parts.append(entry.location)
                for bullet in entry.bullets:
                    parts.append(f"  · {bullet.text}")
    return "\n".join(parts).strip() + "\n"


__all__ = [
    "CVProfile",
    "CVSection",
    "CVEntry",
    "CVBullet",
    "CVItem",
    "PersonalInfo",
    "Enlace",
    "read_cv",
    "SECTION_TYPES",
]
