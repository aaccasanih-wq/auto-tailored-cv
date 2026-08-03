"""Job-summarizer pass — the FIRST LLM call per offer (new in the redesign).

Reduces the raw job description (typically 300-600 words, plus the risk of
prompt-injection hidden inside a third-party posting) to a small structured
JSON computed ONCE per offer and cached as `job_summary.json` next to the
other output files. From then on, tailor / evaluate / repair receive only this
summary instead of re-sending the raw description (saves the biggest chunk of
the ~134k tokens per CV reported before the optimization).

Security: the raw job text is processed ONLY here, and the system prompt
(`prompts/job_summarizer_system.txt`) explicitly declares the job text to be
untrusted DATA, never instructions.
"""

from __future__ import annotations

import json
from typing import Any

from src.config import settings
from src.tailor.llm_client import LLMClient, LLMResponse
from src.tailor.prompts import JobInfo, JobSummary, build_job_summarizer_prompt
from src.utils.logging import get_logger

log = get_logger(__name__)


def summarize_job(
    client: LLMClient,
    job: JobInfo,
    model: str | None = None,
    temperature: float = 0.1,
) -> JobSummary:
    """Run the job-summarizer pass. Returns a JobSummary (possibly empty)."""
    model = model or settings.llm_model_evaluator
    system, user = build_job_summarizer_prompt(job)
    log.info("job_summary: model=%s job=%s/%s", model, job.company, job.title)
    response = client.chat(
        model=model,
        system=system,
        user=user,
        json_mode=True,
        temperature=temperature,
        tag="job_summary",
    )
    return parse_job_summary(response)


def parse_job_summary(response: LLMResponse) -> JobSummary:
    """Parse + sanitize the job-summarizer JSON response."""
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
        log.error("job summarizer returned invalid JSON: %s", e)
        return JobSummary()
    if not isinstance(data, dict):
        return JobSummary()
    return JobSummary.from_dict(data)


def save_job_summary(summary: JobSummary, path: Any) -> None:
    import json as _json
    from pathlib import Path
    Path(path).write_text(
        _json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_job_summary(path: Any) -> JobSummary | None:
    """Load a cached job_summary.json; returns None if absent/unparseable."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    try:
        import json as _json
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return JobSummary.from_dict(data)


__all__ = ["summarize_job", "parse_job_summary", "save_job_summary", "load_job_summary"]
