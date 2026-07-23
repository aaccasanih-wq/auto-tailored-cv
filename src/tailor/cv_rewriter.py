"""Tailor pass — first LLM rewrite of the base CV against a job posting.

The contract for the tailored JSON output (the ANALYSIS.JSON schema) is:

  {
    "summary": "<one line>",
    "sections": [
      {
        "title": "<SECTION TITLE>",
        "kind": "educacion" | "experiencia" | "proyectos" | "habilidades",
        "entries": [
          {
            "titulo": "<immutable>",
            "fecha": "<immutable>",
            "subtitulo": "<immutable>",
            "descriptor": "<editable for proyectos; immutable elsewhere>",
            "enlaces": [{"texto": "...", "url": "..."}],   # protected
            "bullets": ["..."]                              # rewritable
          }
        ],
        "table": [["label", "value"], ...]                 # habilidades only
      }
    ]
  }

The LLM is NEVER shown `enlaces` / URLs — see prompts._strip_enlaces_for_llm.
After the LLM returns, `_reinject_enlaces` copies the protected URLs back from
the base CV into the tailored JSON, byte-identical.

`_validate_shape` checks section titles/order/kind, entry counts per section,
bullet counts per entry, and skills-table shape. The URLs are verified to be
untouched (by virtue of being re-injected from base, never trusted from LLM).
"""

from __future__ import annotations

import json
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
) -> TailorResult:
    model = model or settings.llm_model_tailor
    system, user = build_tailor_prompt(base_cv, job)
    log.info("tailor: model=%s job=%s/%s", model, job.company, job.title)
    response = client.chat(
        model=model, system=system, user=user, json_mode=True, temperature=temperature
    )
    tailored = _parse_json_loose(response.content)
    warnings = _validate_shape(tailored, base_cv)
    if warnings:
        log.warning("tailor produced %d shape warning(s): %s", len(warnings), warnings[:3])
    _reinject_enlaces(tailored, base_cv)
    return TailorResult(tailored_json=tailored, raw_response=response, shape_warnings=warnings)


def _parse_json_loose(content: str) -> dict[str, Any]:
    """Parse JSON; if fenced in a markdown ``` block, strip the fence first."""
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    return json.loads(text)


# --------------------------------------------------------------------------- #
# URL re-injection (link protection)                                          #
# --------------------------------------------------------------------------- #


def _reinject_enlaces(tailored: dict[str, Any], base_cv: CVProfile) -> None:
    """After the LLM returns its JSON (NOT containing `enlaces`), copy the
    protected link arrays back from the base CV — byte-identical.

    Matching is by `titulo` (project/experience title) so that reordering or
    removal of entries (allowed for proyectos) still correctly maps each
    tailored entry to its base counterpart's links.

    This guarantees URLs never reach the LLM and never leave the pipeline
    modified. Idempotent: if the tailored JSON already has enlaces arrays
    (e.g. because the LLM echoed something), they are OVERWRITTEN with the
    base values — so a malicious / buggy LLM cannot tamper with URLs.
    """
    tailored_sections = tailored.get("sections", []) or []
    base_sections = base_cv.sections
    for i, base_s in enumerate(base_sections):
        if i >= len(tailored_sections):
            break
        tailored_s = tailored_sections[i] or {}
        if base_s.kind == "habilidades":
            # skills-table sections have no enlaces.
            tailored_s.pop("enlaces", None)
            continue
        # Build a titulo → enlaces map from the base section entries.
        base_by_titulo: dict[str, list[dict[str, str]]] = {}
        for base_entry in base_s.entries:
            key = (base_entry.titulo or "").strip()
            base_by_titulo[key] = [e.to_dict() for e in base_entry.enlaces]
        tailored_entries = tailored_s.get("entries", []) or []
        for t_entry in tailored_entries:
            if not isinstance(t_entry, dict):
                continue
            t_titulo = (t_entry.get("titulo") or "").strip()
            enlaces = base_by_titulo.get(t_titulo, [])
            if enlaces:
                t_entry["enlaces"] = enlaces
            else:
                t_entry.pop("enlaces", None)


# --------------------------------------------------------------------------- #
# Shape validation                                                            #
# --------------------------------------------------------------------------- #


def _validate_shape(tailored: dict[str, Any], base_cv: CVProfile) -> list[str]:
    """Return a list of human-readable shape warnings. Empty list = OK.

    For "proyectos" sections, fewer entries (removals) and reordering are
    ALLOWED — only "added a non-existent project" is flagged. For
    "experiencia" / "educacion" sections, the entry list must match 1:1.
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

    # Summary template rule (only when the base CV uses the 'En búsqueda...' template)
    base_summary = (base_cv.summary or "").strip()
    if base_summary.startswith("En búsqueda de un puesto en"):
        tailored_summary = (tailored.get("summary") or "").strip()
        if not tailored_summary.startswith("En búsqueda de un puesto en"):
            warnings.append(
                "summary must start with 'En búsqueda de un puesto en '"
                f" (got: {tailored_summary[:80]!r})"
            )
        elif tailored_summary.count(" · ") < 2:
            warnings.append(
                "summary must have at least two ' · ' separators"
                f" (got: {tailored_summary!r})"
            )

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
        if tailored_s.get("kind") != base_s.kind:
            warnings.append(
                f"section '{base_s.title}' kind mismatch: base={base_s.kind!r} "
                f"tailored={tailored_s.get('kind')!r}"
            )
        if base_s.kind == "habilidades":
            bt = base_s.table
            tt = tailored_s.get("table", []) or []
            if len(tt) != len(bt):
                warnings.append(
                    f"section '{base_s.title}' table row count differs: "
                    f"base={len(bt)} tailored={len(tt)}"
                )
                continue
            for ri, (brow, trow) in enumerate(zip(bt, tt)):
                if not isinstance(trow, list) or len(trow) != len(brow):
                    warnings.append(
                        f"section '{base_s.title}' table row {ri} col count differs"
                    )
        elif base_s.kind == "proyectos":
            # Flexible: entries may be removed or reordered, but NOT invented.
            base_titulos = {(e.titulo or "").strip() for e in base_s.entries}
            te = tailored_s.get("entries", []) or []
            for ei, t_entry in enumerate(te):
                if not isinstance(t_entry, dict):
                    continue
                t_titulo = (t_entry.get("titulo") or "").strip()
                if t_titulo and t_titulo not in base_titulos:
                    warnings.append(
                        f"section '{base_s.title}' entry {ei}: titulo '{t_titulo}' "
                        f"not found in base CV (invented project)"
                    )
                # Check empty-parens descriptor
                t_desc = (t_entry.get("descriptor") or "").strip()
                if t_desc in ("()", ):
                    warnings.append(
                        f"section '{base_s.title}' entry {ei}: descriptor is empty "
                        f"parentheses '()' — should be empty string instead"
                    )
                stray_enlaces = t_entry.get("enlaces")
                if stray_enlaces:
                    warnings.append(
                        f"section '{base_s.title}' entry {ei}: tailored LLM output "
                        f"contained 'enlaces' (must be omitted; URLs are protected)."
                    )
        else:
            # experiencia / educacion: strict 1:1 match
            be = base_s.entries
            te = tailored_s.get("entries", []) or []
            if len(te) != len(be):
                warnings.append(
                    f"section '{base_s.title}' entry count differs: "
                    f"base={len(be)} tailored={len(te)}"
                )
                continue
            for ei, (b_entry, t_entry) in enumerate(zip(be, te)):
                tb = t_entry.get("bullets", []) or []
                if len(tb) != len(b_entry.bullets):
                    warnings.append(
                        f"section '{base_s.title}' entry {ei} bullet count differs: "
                        f"base={len(b_entry.bullets)} tailored={len(tb)}"
                    )
                # Immutable field drift (titulo/fecha/subtitulo/descriptor)
                for immutable in ("titulo", "fecha", "subtitulo", "descriptor"):
                    b_val = getattr(b_entry, immutable) or ""
                    t_val = (t_entry.get(immutable) or "") if isinstance(t_entry, dict) else ""
                    if t_val and t_val != b_val:
                        warnings.append(
                            f"section '{base_s.title}' entry {ei} immutable field "
                            f"'{immutable}' modified: base={b_val!r} tailored={t_val!r}"
                        )
                # URL tampering heuristic: LLM output must NOT contain enlaces
                # before _reinject_enlaces runs. _validate_shape is invoked
                # BEFORE reinjection in tailor_cv, so we flag any stray enlaces.
                stray_enlaces = t_entry.get("enlaces") if isinstance(t_entry, dict) else None
                if stray_enlaces:
                    warnings.append(
                        f"section '{base_s.title}' entry {ei}: tailored LLM output "
                        f"contained 'enlaces' (must be omitted; URLs are protected)."
                    )
    return warnings


def save_tailored_json(result: TailorResult, path: Path) -> None:
    path.write_text(
        json.dumps(result.tailored_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = ["TailorResult", "tailor_cv", "save_tailored_json", "_validate_shape", "_reinject_enlaces"]