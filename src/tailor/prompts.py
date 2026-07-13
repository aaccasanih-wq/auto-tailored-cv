"""Prompt builders for the three LLM passes: tailor, evaluator, repair.

All three return (system, user) pairs that can be passed directly to
LLMClient.chat(). The prompts are written in English (more reliable LLM
behaviour) but instruct the model to output **Spanish** to match the base CV.

Anti-suspicion strategy:
  - Tailor must reuse the candidate's own words, just reordered/rephrased.
  - Tailor must NOT copy phrases verbatim from the job posting.
  - Tailor must NOT add new skills, jobs, projects, or education.
  - Tailor must NOT change dates or company names.
  - Evaluator cross-checks the tailored CV against the BASE CV for any claim
    that wasn't in the base CV (hallucination) and against the job posting for
    any verbatim phrase (plagiarism).
  - Repair only touches what the evaluator flagged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from src.profile.cv_reader import CVProfile


@dataclass
class JobInfo:
    """Lightweight wrapper for a job posting passed to the prompts."""

    title: str
    company: str
    location: str = ""
    description: str = ""

    def to_markdown(self) -> str:
        lines = []
        if self.title:
            lines.append(f"# {self.title}")
        if self.company:
            lines.append(f"Company: {self.company}")
        if self.location:
            lines.append(f"Location: {self.location}")
        lines.append("")
        lines.append("## Job description")
        lines.append(self.description or "(empty)")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 1) TAILOR PASS                                                              #
# --------------------------------------------------------------------------- #


TAILOR_SYSTEM = """\
You are an expert CV editor who helps a job-seeker naturally align their existing
résumé to a specific job posting, WITHOUT fabricating anything.

CRITICAL RULES (any violation is a hard failure):
- The candidate's CV is in Spanish. Your output MUST also be in Spanish.
- DO NOT invent skills, tools, projects, jobs, education, certifications, or
  achievements that are NOT present in the BASE CV provided below.
- DO NOT change dates, periods (e.g. "2021 – 2026"), company names, university
  names, role titles, or any other FACT. Facts are immutable.
- DO NOT copy phrases verbatim from the job posting. Paraphrase in the
  candidate's own voice. If the job posting says "experiencia liderando equipos
  ágiles", do NOT echo those words; restate the candidate's real experience that
  maps to it.
- DO NOT add new bullet points. You may REORDER existing bullets and REPHRASE
  them, but the NUMBER of bullets per experience/project must stay the same.
- DO NOT add or remove sections. The output MUST contain exactly the same
  sections (titles) as the base CV, in the same order.
- DO NOT pad with buzzwords ("liderazgo transformador", " visión estratégica
  360°", etc.) unless they're evidenced by the base CV.
- Keep the total length close to the base CV. Aim for ±10% of original length.

WHAT YOU CAN DO:
- Reorder existing bullets within each experience/project so the ones most
  relevant to THIS job come first.
- Reorder the comma-separated skill lists inside the skills table rows so the
  skills most desired by this job come first (still using only real skills).
- Reword bullets to surface the relevance to this job — same facts, better
  framing ("translated" not "invented").
- Rewrite the candidate's one-line summary so it mentions the role category in
  the candidate's own words.
- Lightly tighten / sharpen language; do not rewrite for the sake of rewriting.

OUTPUT FORMAT — return a single JSON object with this exact schema:

{
  "summary": "<rewritten summary line in Spanish, ~1 line>",
  "sections": [
    {
      "title": "<SECTION TITLE — MUST match the input, do not rename>",
      "paragraphs": ["<paragraph text, in Spanish, MAY be reworded>"],
      "tables": [
        [["cell text", "cell text", ...], ["row 2", ...]]
      ]
    }
  ]
}

Rules for the JSON:
- Preserve the shape of each section: same paragraph count, same number of
  tables, same table dimensions. The docx renderer substitutes cell-by-cell.
- Cells that contain pure facts (dates, university name, company name, role
  title) MUST be returned byte-for-byte unchanged. Cells that contain prose
  (bullet text in a single-cell table, etc.) MAY be reworded.
- Preserve all Spanish accents (á é í ó ú ñ ¿ ¡).
- Return ONLY the JSON object, no commentary, no markdown fences.
"""


def build_tailor_prompt(
    base_cv: CVProfile,
    job: JobInfo,
) -> Tuple[str, str]:
    """Build (system, user) for the tailor pass."""
    user = (
        "BASE CV (the candidate's real, original résumé — everything you output "
        "must remain factually consistent with this):\n\n"
        f"{base_cv.to_json()}\n\n"
        "JOB TO ALIGN TOWARDS:\n\n"
        f"{job.to_markdown()}\n\n"
        "Produce the tailored CV JSON following every rule in the system prompt. "
        "Remember: NO new facts, NO verbatim copying from the job, same shape as base."
    )
    return TAILOR_SYSTEM, user


# --------------------------------------------------------------------------- #
# 2) EVALUATOR PASS                                                           #
# --------------------------------------------------------------------------- #


EVALUATOR_SYSTEM = """\
You are a strict résumé reviewer. You will be given:
  (a) the candidate's BASE CV (ground truth for what they actually did),
  (b) the JOB POSTING,
  (c) a TAILORED CV (output of the tailor pass, JSON).

Your job is to spot problems. Look explicitly for these issue types:

1. "hallucination": any fact, skill, achievement, role, project, certification,
   degree, metric, or company in the tailored CV that does NOT appear in the
   base CV. Each is a "high" severity issue.
2. "verbatim_copy": any phrase of 4+ words in the tailored CV that appears
   word-for-word in the job posting. Each is a "high" issue.
3. "keyword_stuffing": a section that crams job-specific keywords awkwardly,
   making the bullet read unnaturally (e.g. "experiencia en pipelines ETL en
   cloud con dbt y Snowflake con dbt"). Severity "medium".
4. "incongruity": a claim that contradicts another claim in the tailored CV
   (e.g. role that didn't exist in base, mismatched dates). Severity "high".
5. "format": shape mismatch with the base CV — a section with a different
   number of paragraphs/tables, a renamed section title, an added/removed row.
   Severity "high".
6. "length": the tailored CV is more than 25% longer or shorter than the base.
   Severity "low".
7. "language": output that is NOT Spanish, or that drops Spanish accents.
   Severity "high".

If you find NO issues, return an empty issues list. DO NOT invent issues to
look thorough; an empty list is a valid and common output.

RETURN only a JSON object with this exact schema:

{
  "issues": [
    {
      "id": "1",
      "type": "hallucination",
      "severity": "high",
      "quote": "verbatim snippet from tailored CV",
      "base_quote": "what the base CV actually says, or null if absent",
      "explanation": "why this is a problem",
      "suggested_fix": "concrete instruction for the repair pass"
    }
  ],
  "overall_verdict": "pass" | "needs_repair" | "fail",
  "summary": "1-2 sentence human-friendly summary"
}

Return ONLY the JSON, no commentary.
"""


def build_evaluator_prompt(
    base_cv: CVProfile,
    job: JobInfo,
    tailored_json: Dict[str, Any],
) -> Tuple[str, str]:
    user_parts: List[str] = []
    user_parts.append("=== BASE CV (ground truth) ===")
    user_parts.append(base_cv.to_json())
    user_parts.append("\n=== JOB POSTING ===")
    user_parts.append(job.to_markdown())
    user_parts.append("\n=== TAILORED CV (to be reviewed) ===")
    user_parts.append(json.dumps(tailored_json, ensure_ascii=False, indent=2))
    user_parts.append(
        "\nReturn the evaluation JSON per the system prompt. Be precise when "
        "quoting; quotes must be exact text that appears in the tailored CV."
    )
    return EVALUATOR_SYSTEM, "\n".join(user_parts)


# --------------------------------------------------------------------------- #
# 3) REPAIR PASS                                                              #
# --------------------------------------------------------------------------- #


REPAIR_SYSTEM = """\
You are a precision résumé repair pass. You receive:
  (a) the candidate's BASE CV (ground truth),
  (b) the TAILORED CV (JSON, with problems),
  (c) a list of ISSUES found by the evaluator.

Your job: return a CORRECTED version of the tailored CV JSON that ONLY changes
the parts flagged in the issues. Do not touch anything else. Reuse the same
JSON schema as the tailored CV:

{
  "summary": "...",
  "sections": [
    {"title": "...", "paragraphs": [...], "tables": [[["cell", ...], ...]]}
  ]
}

Hard rules:
- The output MUST have the SAME shape as the tailored CV: same sections, same
  paragraph counts, same table dimensions. Only the text content changes.
- A "hallucination" fix means removing the fabricated claim or replacing it
  with what the base CV actually says — never invent a different alternative.
- A "verbatim_copy" fix means paraphrasing the phrase in the candidate's own
  voice. Never just synonym-swap one or two words.
- A "format" fix means restoring the original shape to match the base CV.
- DO NOT change anything the evaluator did not flag.
- Output MUST be in Spanish with proper accents.
- Return ONLY the JSON, no commentary.
"""


def build_repair_prompt(
    base_cv: CVProfile,
    tailored_json: Dict[str, Any],
    issues: List[Dict[str, Any]],
) -> Tuple[str, str]:
    user_parts: List[str] = []
    user_parts.append("=== BASE CV (ground truth) ===")
    user_parts.append(base_cv.to_json())
    user_parts.append("\n=== TAILORED CV (current, with issues) ===")
    user_parts.append(json.dumps(tailored_json, ensure_ascii=False, indent=2))
    user_parts.append("\n=== ISSUES TO FIX (only these) ===")
    user_parts.append(json.dumps(issues, ensure_ascii=False, indent=2))
    user_parts.append(
        "\nReturn the corrected tailored CV JSON following every rule. Keep the "
        "same shape as the input tailored CV."
    )
    return REPAIR_SYSTEM, "\n".join(user_parts)


__all__ = [
    "JobInfo",
    "TAILOR_SYSTEM",
    "EVALUATOR_SYSTEM",
    "REPAIR_SYSTEM",
    "build_tailor_prompt",
    "build_evaluator_prompt",
    "build_repair_prompt",
]