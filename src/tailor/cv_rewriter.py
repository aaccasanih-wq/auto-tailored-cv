"""Tailor pass — first LLM rewrite of the base CV against a job posting.

The contract for the tailored JSON output (the ANALYSIS.JSON schema) is:

  {
    "summary": "<one line/paragraph, rewritable>",
    "sections": [
      {
        "id": "<immutable>",
        "title": "<immutable>",
        "type": "entry_block" | "simple_list" | "text_block",
        "reorderable": true|false,
        "entries": [                          # entry_block only
          {
            "heading": "<immutable>", "subheading": "<immutable>",
            "location": "<immutable>", "dates": "<immutable>",
            "links": [{"label": "...", "url": "..."}],   # protected
            "bullets": [{"text": "...", "tags": [...]}]  # rewritable
          }
        ],
        "items": [{"text": "...", "tags": [...]}],   # simple_list only
        "text": "..."                                # text_block only
      }
    ]
  }

The LLM is NEVER shown `links` / URLs — see prompts._strip_links_for_llm.
After the LLM returns, `_reinject_links` copies the protected URLs back from
the base CV into the tailored JSON, byte-identical, matching entries by
`heading` (so reordering/removal in `reorderable: true` sections still maps
correctly).

`_validate_shape` checks section titles/order/type, entry counts per section
(driven by `section.reorderable`), immutable-field drift, and deterministically
drops empty bullets / empty-headed entries WITHOUT any LLM call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import settings
from src.profile.cv_reader import CVProfile
from src.tailor.llm_client import LLMClient, LLMResponse
from src.tailor.prompts import JobInfo, build_tailor_prompt
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class TailorResult:
    tailored_json: dict[str, Any]
    raw_response: LLMResponse
    shape_warnings: list[str] = field(default_factory=list)


def tailor_cv(
    client: LLMClient,
    base_cv: CVProfile,
    job: JobInfo,
    model: str | None = None,
    temperature: float = 0.3,
    user_preferences: str = "",
) -> TailorResult:
    model = model or settings.llm_model_tailor
    system, user = build_tailor_prompt(base_cv, job, user_preferences)
    log.info("tailor: model=%s job=%s/%s", model, job.company, job.title)
    last_result: TailorResult | None = None
    for attempt in range(1, 4):
        response = client.chat(
            model=model,
            system=system,
            user=user if attempt == 1 else (
                user + "\n\nIMPORTANT: The previous response was incomplete. "
                "Return the complete JSON object now, including a non-empty "
                "`sections` array. Do not return `{}`, a status message, or an envelope."
            ),
            json_mode=True,
            temperature=temperature,
            tag="tailor" if attempt == 1 else f"tailor_retry_{attempt}",
        )
        tailored = _parse_json_loose(response.content)
        warnings = _validate_shape(tailored, base_cv)
        if warnings:
            log.warning("tailor produced %d shape warning(s): %s", len(warnings), warnings[:3])
        _reinject_links(tailored, base_cv)
        last_result = TailorResult(
            tailored_json=tailored, raw_response=response, shape_warnings=warnings,
        )
        if isinstance(tailored.get("sections"), list) and tailored["sections"]:
            return last_result
        if attempt < 3:
            log.warning(
                "tailor attempt %d returned no sections for %s; retrying",
                attempt, job.title,
            )
    log.error("tailor returned no sections after 3 attempts for %s", job.title)
    return last_result  # type: ignore[return-value]


def _parse_json_loose(content: str) -> dict[str, Any]:
    """Parse JSON; if fenced in a markdown ``` block, strip the fence first.

    Some OpenAI-compatible backends (e.g. the OpenCode "go" tier) occasionally
    wrap the real payload in a junk envelope like `{" .json": "<json string>"}`
    or inject junk keys (e.g. `/**/`) alongside it. The envelope is unwrapped
    here (up to 2 levels deep); stray junk keys are harmless because validation
    only reads `sections`/`summary`.
    """
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    parsed = json.loads(text)
    for _ in range(2):
        if not isinstance(parsed, dict):
            break
        if parsed.get("sections") is not None or parsed.get("summary") is not None:
            break
        unwrapped = None
        for value in parsed.values():
            if not isinstance(value, str):
                continue
            try:
                candidate = json.loads(value.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                unwrapped = candidate
                break
        if unwrapped is None:
            break
        parsed = unwrapped
    return _normalize_json_keys(parsed)


def _normalize_json_keys(value: Any) -> Any:
    """Normalize provider quirks such as `/sections` and `/summary` keys."""
    if isinstance(value, dict):
        return {
            (key.lstrip("/") if isinstance(key, str) else key): _normalize_json_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_json_keys(item) for item in value]
    return value


# --------------------------------------------------------------------------- #
# URL re-injection (link protection)                                          #
# --------------------------------------------------------------------------- #


def _reinject_links(tailored: dict[str, Any], base_cv: CVProfile) -> None:
    """After the LLM returns its JSON (NOT containing `links`), copy the
    protected link arrays back from the base CV — byte-identical.

    Matching is by `heading` so that reordering or removal of entries (allowed
    for `reorderable: true` sections) still maps each tailored entry to its
    base counterpart's links.

    Idempotent: if the tailored JSON already has `links`/`enlaces` arrays
    (e.g. because the LLM echoed something), they are OVERWRITTEN with the
    base values — a malicious / buggy LLM cannot tamper with URLs.
    """
    tailored_sections = tailored.get("sections", []) or []
    for i, base_s in enumerate(base_cv.sections):
        if i >= len(tailored_sections):
            break
        tailored_s = tailored_sections[i] or {}
        if base_s.type in ("simple_list", "text_block"):
            # These section types carry no links.
            tailored_s.pop("links", None)
            tailored_s.pop("enlaces", None)
            continue
        # Build a heading → links map from the base section entries.
        base_by_heading: dict[str, list[dict[str, str]]] = {}
        for base_entry in base_s.entries:
            key = (base_entry.heading or "").strip()
            base_by_heading[key] = [link.to_dict() for link in base_entry.links]
        tailored_entries = tailored_s.get("entries", []) or []
        for t_entry in tailored_entries:
            if not isinstance(t_entry, dict):
                continue
            t_heading = (t_entry.get("heading") or "").strip()
            links = base_by_heading.get(t_heading, [])
            if links:
                t_entry["links"] = links
            else:
                t_entry.pop("links", None)
            t_entry.pop("enlaces", None)


# --------------------------------------------------------------------------- #
# Deterministic empty-content cleanup (no LLM involved)                       #
# --------------------------------------------------------------------------- #

_EMPTY_SEPARATOR_RE = re.compile(r"^[\s\-•·–—_—]+$")


def _bullet_has_text(bullet: Any) -> bool:
    """True if a bullet carries real text (not just a lone separator)."""
    if isinstance(bullet, str):
        text = bullet.strip()
    elif isinstance(bullet, dict):
        text = (bullet.get("text") or "").strip()
    else:
        return False
    if not text:
        return False
    if _EMPTY_SEPARATOR_RE.fullmatch(text):
        return False
    return True


def _clean_empty_content(tailored: dict[str, Any]) -> list[str]:
    """Deterministically drop empty bullets and empty-headed entries from the
    tailored JSON (mutates it) and return warnings for what was dropped. This
    covers the "bullet with a dash and no text" risk in the final PDF without
    relying on the (LLM) evaluator to catch it.

    - `entry_block`: entries with an empty `heading` are dropped; bullets with
      empty / separator-only `text` are dropped.
    - `simple_list`: items with empty `text` are dropped.
    - `text_block`: untouched.
    """
    warnings: list[str] = []
    for s in tailored.get("sections", []) or []:
        if not isinstance(s, dict):
            continue
        title = s.get("title") or ""
        s_type = s.get("type") or ""
        if s_type == "simple_list":
            kept_items = []
            for ii, item in enumerate(s.get("items", []) or []):
                text = (item.get("text") or "").strip() if isinstance(item, dict) else ""
                if not text:
                    warnings.append(f"section '{title}' item {ii}: texto vacío — descartado")
                    continue
                kept_items.append(item)
            s["items"] = kept_items
        elif s_type == "entry_block":
            kept_entries: list[Any] = []
            for ei, entry in enumerate(s.get("entries", []) or []):
                if not isinstance(entry, dict):
                    kept_entries.append(entry)
                    continue
                if not (entry.get("heading") or "").strip():
                    warnings.append(
                        f"section '{title}' entry {ei}: heading vacío — entrada descartada"
                    )
                    continue
                kept_bullets = []
                for bi, bullet in enumerate(entry.get("bullets", []) or []):
                    if not _bullet_has_text(bullet):
                        warnings.append(
                            f"section '{title}' entry {ei} bullet {bi}: texto vacío "
                            f"(solo separador) — descartado"
                        )
                        continue
                    kept_bullets.append(bullet)
                entry["bullets"] = kept_bullets
                kept_entries.append(entry)
            s["entries"] = kept_entries
    return warnings


# --------------------------------------------------------------------------- #
# Shape validation                                                            #
# --------------------------------------------------------------------------- #


def _validate_shape(tailored: dict[str, Any], base_cv: CVProfile) -> list[str]:
    """Return a list of human-readable shape warnings. Empty list = OK.

    Generic by section `type` and driven by `section.reorderable`:
      - `entry_block` reorderable: entries may be removed/reordered (not
        invented); bullets editable.
      - `entry_block` non-reorderable: strict 1:1 entries/order/bullets;
        immutable fields must not drift.
      - `simple_list` / `text_block`: flexible (no shape constraints).

    Also performs the deterministic empty-content cleanup (see
    `_clean_empty_content`) BEFORE the structural checks so the final JSON is
    always PDF-safe without an LLM.
    """
    warnings: list[str] = []
    if not isinstance(tailored, dict):
        warnings.append("tailored output is not a JSON object")
        return warnings
    if "sections" not in tailored or not isinstance(tailored["sections"], list):
        warnings.append("tailored output missing 'sections' list")
        return warnings
    if "summary" not in tailored or not isinstance(tailored["summary"], str):
        warnings.append("tailored output missing 'summary' string")

    # Deterministic cleanup (marks + discards empty bullets/entries).
    warnings.extend(_clean_empty_content(tailored))

    expected_titles = [s.title for s in base_cv.sections]
    got_titles = [s.get("title", "") for s in tailored["sections"]]
    if expected_titles != got_titles:
        warnings.append(
            f"section titles/order mismatch — expected {expected_titles}, got {got_titles}"
        )

    for i, base_s in enumerate(base_cv.sections):
        if i >= len(tailored["sections"]):
            break
        tailored_s = tailored["sections"][i] or {}
        t_type = tailored_s.get("type") or base_s.type
        if tailored_s.get("type") != base_s.type:
            warnings.append(
                f"section '{base_s.title}' type mismatch: base={base_s.type!r} "
                f"tailored={tailored_s.get('type')!r}"
            )
        if t_type in ("simple_list", "text_block"):
            # Flexible: freely reorderable / reformulable.
            continue
        # --- entry_block ---
        entries = tailored_s.get("entries", []) or []
        if base_s.reorderable:
            # Flexible entry list: removals + reordering allowed; invention flagged.
            base_headings = {(e.heading or "").strip() for e in base_s.entries}
            for ei, t_entry in enumerate(entries):
                if not isinstance(t_entry, dict):
                    continue
                t_heading = (t_entry.get("heading") or "").strip()
                if t_heading and t_heading not in base_headings:
                    warnings.append(
                        f"section '{base_s.title}' entry {ei}: heading '{t_heading}' "
                        f"not found in base CV (invented entry)"
                    )
                _warn_stray_links(warnings, base_s.title, ei, t_entry)
        else:
            # Strict 1:1.
            be = base_s.entries
            if len(entries) != len(be):
                warnings.append(
                    f"section '{base_s.title}' entry count differs: "
                    f"base={len(be)} tailored={len(entries)}"
                )
                continue
            for ei, (b_entry, t_entry) in enumerate(zip(be, entries)):
                if not isinstance(t_entry, dict):
                    continue
                tb = t_entry.get("bullets", []) or []
                if len(tb) != len(b_entry.bullets):
                    warnings.append(
                        f"section '{base_s.title}' entry {ei} bullet count differs: "
                        f"base={len(b_entry.bullets)} tailored={len(tb)}"
                    )
                for immutable in ("heading", "subheading", "location", "dates"):
                    b_val = getattr(b_entry, immutable) or ""
                    t_val = (t_entry.get(immutable) or "") if isinstance(t_entry, dict) else ""
                    if t_val and t_val != b_val:
                        warnings.append(
                            f"section '{base_s.title}' entry {ei} immutable field "
                            f"'{immutable}' modified: base={b_val!r} tailored={t_val!r}"
                        )
                _warn_stray_links(warnings, base_s.title, ei, t_entry)
    return warnings


def _warn_stray_links(
    warnings: list[str], title: str, ei: int, t_entry: dict[str, Any]
) -> None:
    """Flag any `links`/`enlaces` array the LLM emitted (they must be omitted;
    URLs are protected and re-injected). Called BEFORE `_reinject_links` runs."""
    for key in ("links", "enlaces"):
        stray = t_entry.get(key)
        if stray:
            warnings.append(
                f"section '{title}' entry {ei}: tailored LLM output contained "
                f"'{key}' (must be omitted; URLs are protected)."
            )


def save_tailored_json(result: TailorResult, path: Path) -> None:
    path.write_text(
        json.dumps(result.tailored_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = [
    "TailorResult",
    "tailor_cv",
    "save_tailored_json",
    "_validate_shape",
    "_reinject_links",
    "_clean_empty_content",
]
