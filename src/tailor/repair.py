"""Repair pass — third LLM call, conditional on evaluator findings.

Consumes: base CV, tailored JSON, issues list from evaluator.
Produces: a corrected tailored JSON, same shape/schema as the tailor output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import settings
from src.profile.cv_reader import CVProfile
from src.tailor.cv_rewriter import _parse_json_loose, _reinject_enlaces, _validate_shape
from src.tailor.llm_client import LLMClient, LLMResponse
from src.tailor.prompts import build_repair_prompt
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RepairResult:
    repaired_json: dict[str, Any]
    raw_response: LLMResponse
    shape_warnings: list[str] = field(default_factory=list)


def repair_cv(
    client: LLMClient,
    base_cv: CVProfile,
    tailored_json: dict[str, Any],
    issues: list[dict[str, Any]],
    model: str | None = None,
    temperature: float = 0.1,
) -> RepairResult:
    """Apply ONLY the fixes flagged by the evaluator. Returns the repaired JSON."""
    model = model or settings.llm_model_evaluator
    system, user = build_repair_prompt(base_cv, tailored_json, issues)
    log.info("repair: model=%s issues=%d", model, len(issues))
    response = client.chat(
        model=model, system=system, user=user, json_mode=True, temperature=temperature
    )
    repaired = _parse_json_loose(response.content)
    warnings = _validate_shape(repaired, base_cv)
    _reinject_enlaces(repaired, base_cv)
    return RepairResult(
        repaired_json=repaired, raw_response=response, shape_warnings=warnings
    )


def save_repaired_json(result: RepairResult, path: Path) -> None:
    path.write_text(
        json.dumps(result.repaired_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )


__all__ = ["RepairResult", "repair_cv", "save_repaired_json"]