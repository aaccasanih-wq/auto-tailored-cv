"""Tailor pass — first LLM rewrite of the base CV against a job posting.

The contract for the tailored JSON output is:
  {
    "summary": "<one line>",
    "sections": [
      {"title": "...", "paragraphs": [...], "tables": [[["cell", ...], ...]]}
    ]
  }
The number of sections, paragraphs per section, and table dimensions MUST
match the base CV. The docx renderer substitutes cell-by-cell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import settings
from src.profile.cv_reader import CVProfile
from src.tailor.llm_client import LLMClient, LLMResponse
from src.tailor.prompts import JobInfo, build_tailor_prompt
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class TailorResult:
    tailored_json: Dict[str, Any]
    raw_response: LLMResponse
    shape_warnings: List[str] = field(default_factory=list)


def tailor_cv(
    client: LLMClient,
    base_cv: CVProfile,
    job: JobInfo,
    model: Optional[str] = None,
    temperature: float = 0.3,
) -> TailorResult:
    model = model or settings.opencode_model_tailor
    system, user = build_tailor_prompt(base_cv, job)
    log.info("tailor: model=%s job=%s/%s", model, job.company, job.title)
    response = client.chat(
        model=model, system=system, user=user, json_mode=True, temperature=temperature
    )
    tailored = _parse_json_loose(response.content)
    warnings = _validate_shape(tailored, base_cv)
    if warnings:
        log.warning("tailor produced %d shape warning(s): %s", len(warnings), warnings[:3])
    return TailorResult(tailored_json=tailored, raw_response=response, shape_warnings=warnings)


def _parse_json_loose(content: str) -> Dict[str, Any]:
    """Parse JSON; if fenced in a markdown ``` block, strip the fence first."""
    text = content.strip()
    if text.startswith("```"):
        # strip first line (```json or ```) and any trailing ```
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    return json.loads(text)


def _validate_shape(tailored: Dict[str, Any], base_cv: CVProfile) -> List[str]:
    """Return a list of human-readable shape warnings. Empty list = OK."""
    warnings: List[str] = []
    if not isinstance(tailored, dict):
        warnings.append("tailored output is not a JSON object")
        return warnings
    if "sections" not in tailored or not isinstance(tailored["sections"], list):
        warnings.append("tailored output missing 'sections' list")
        return warnings
    if "summary" not in tailored or not isinstance(tailored["summary"], str):
        warnings.append("tailored output missing 'summary' string")

    expected_titles = [s.title for s in base_cv.sections]
    got_titles = [s.get("title", "") for s in tailored["sections"]]
    if expected_titles != got_titles:
        warnings.append(
            f"section titles/order mismatch — expected {expected_titles}, got {got_titles}"
        )

    for i, (base_s, tailored_s) in enumerate(zip(base_cv.sections, tailored["sections"])):
        if i >= len(base_cv.sections):
            break
        bp = base_s.paragraphs
        tp = tailored_s.get("paragraphs", []) or []
        if len(tp) != len(bp):
            warnings.append(
                f"section '{base_s.title}' paragraph count differs: base={len(bp)} tailored={len(tp)}"
            )
        bt = base_s.tables
        tt = tailored_s.get("tables", []) or []
        if len(tt) != len(bt):
            warnings.append(
                f"section '{base_s.title}' table count differs: base={len(bt)} tailored={len(tt)}"
            )
            continue
        for bi, (btbl, ttbl) in enumerate(zip(bt, tt)):
            if not isinstance(ttbl, list) or len(ttbl) != len(btbl):
                warnings.append(f"section '{base_s.title}' table {bi} row count differs")
                continue
            for ri, (brow, trow) in enumerate(zip(btbl, ttbl)):
                if not isinstance(trow, list) or len(trow) != len(brow):
                    warnings.append(
                        f"section '{base_s.title}' table {bi} row {ri} col count differs"
                    )
    return warnings


def save_tailored_json(result: TailorResult, path: Path) -> None:
    path.write_text(
        json.dumps(result.tailored_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = ["TailorResult", "tailor_cv", "save_tailored_json"]