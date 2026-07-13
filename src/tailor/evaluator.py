"""Evaluator pass — second LLM call that reviews a tailored CV for issues.

Consumes: base CV, job posting, tailored JSON.
Produces: an EvaluationResult with a list of issues and an overall verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import settings
from src.profile.cv_reader import CVProfile
from src.tailor.llm_client import LLMClient, LLMResponse
from src.tailor.prompts import JobInfo, build_evaluator_prompt
from src.utils.logging import get_logger

log = get_logger(__name__)


SEVERITIES = {"high", "medium", "low"}


@dataclass
class EvaluationResult:
    verdict: str            # "pass" | "needs_repair" | "fail"
    issues: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    raw_response: Optional[LLMResponse] = None

    @property
    def needs_repair(self) -> bool:
        return any(i.get("severity") == "high" for i in self.issues) or self.verdict == "needs_repair"


def evaluate(
    client: LLMClient,
    base_cv: CVProfile,
    job: JobInfo,
    tailored_json: Dict[str, Any],
    model: Optional[str] = None,
    temperature: float = 0.1,
) -> EvaluationResult:
    """Run the evaluator pass. Always returns an EvaluationResult (possibly with empty issues)."""
    model = model or settings.opencode_model_evaluator
    system, user = build_evaluator_prompt(base_cv, job, tailored_json)
    log.info("evaluate: model=%s", model)
    response = client.chat(
        model=model, system=system, user=user, json_mode=True, temperature=temperature
    )
    return parse_evaluation(response)


def parse_evaluation(response: LLMResponse) -> EvaluationResult:
    """Parse + lightly sanitize the evaluator JSON response."""
    text = response.content.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.error("evaluator returned invalid JSON: %s", e)
        return EvaluationResult(
            verdict="fail",
            issues=[{
                "id": "0",
                "type": "format",
                "severity": "high",
                "quote": "",
                "base_quote": None,
                "explanation": f"evaluator JSON parse failed: {e}",
                "suggested_fix": "repair: re-run the tailor or evaluator",
            }],
            summary="evaluator JSON parse failed",
            raw_response=response,
        )
    issues = data.get("issues", []) or []
    verdict = data.get("overall_verdict", "needs_repair")
    if verdict not in {"pass", "needs_repair", "fail"}:
        verdict = "needs_repair"
    # Normalize severities to lowercase
    for issue in issues:
        sev = (issue.get("severity") or "low").lower()
        if sev not in SEVERITIES:
            sev = "low"
        issue["severity"] = sev
    return EvaluationResult(
        verdict=verdict,
        issues=issues,
        summary=data.get("summary", ""),
        raw_response=response,
    )


def save_evaluation_json(result: EvaluationResult, path: Path) -> None:
    payload = {
        "verdict": result.verdict,
        "summary": result.summary,
        "issues": result.issues,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["EvaluationResult", "evaluate", "parse_evaluation", "save_evaluation_json"]