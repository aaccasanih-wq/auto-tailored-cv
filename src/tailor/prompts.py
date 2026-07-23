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

URL/hyperlink protection:
  - The base CV's hyperlinks are sent to the LLM ONLY as visible text,
    never as URLs. The prompt explicitly says: the `url` field is out of
    scope; do not modify or invent URLs.
  - After the tailor returns, the original `enlaces` arrays are re-injected
    (`tailor_cv._reinject_enlaces`) so `analysis.json` keeps the protected
    URLs byte-identical to the base CV.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.profile.cv_reader import CVProfile, CVSection

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _strip_enlaces_for_llm(base_cv: CVProfile) -> dict[str, Any]:
    """Return a JSON-serializable view of base_cv suitable for the LLM:
    same shape as `CVProfile.to_dict()` but WITHOUT any `enlaces` /
    `contact_enlaces` URL arrays.

    The LLM still sees the visible link text (e.g. the descriptor
    "(Dashboard)" or the contact line "Sitio web" / "Mis Proyectos") which
    are immutable — it just never sees the underlying URLs.
    """
    def section_view(s: CVSection) -> dict[str, Any]:
        d = s.to_dict()
        for entry in d.get("entries", []) or []:
            entry.pop("enlaces", None)
        return d

    return {
        "name": base_cv.name,
        "contact": base_cv.contact,
        # Deliberately omitted: contact_enlaces (URLs protected)
        "summary": base_cv.summary,
        "sections": [section_view(s) for s in base_cv.sections],
    }


# --------------------------------------------------------------------------- #
# JobInfo                                                                     #
# --------------------------------------------------------------------------- #


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
You are an expert CV editor who aligns a candidate's existing résumé to a job
posting WITHOUT fabricating anything. The candidate's CV is Spanish; your
output MUST also be in Spanish with all accents preserved (á é í ó ú ñ ¿ ¡).

CRITICAL RULES (any violation is a hard failure):
- DO NOT invent skills, tools, projects, jobs, education, certifications, or
  achievements that are NOT in the BASE CV.
- DO NOT change dates, company names, university names, role titles, project
  titles, or any other FACT.
- DO NOT copy phrases verbatim from the job posting. Paraphrase in the
  candidate's own voice.
- DO NOT pad with buzzwords ("liderazgo transformador", "visión estratégica
  360°") unless evidenced by the base CV.
- Keep total length within ±10% of the base CV.

IMMUTABLE FIELDS (return byte-for-byte unchanged):
- `name`, `contact`, every section `title` and `kind`.
- For every entry: `titulo`, `fecha`, `subtitulo`.
- URLs are protected: the base CV is given to you WITHOUT `url` arrays; your
  output MUST likewise OMIT `enlaces` (the orchestrator re-injects URLs
  post-rewrite). DO NOT invent or echo any URL anywhere.
- For experiencia/educacion sections: also keep `descriptor` byte-for-byte,
  and DO NOT add or remove entries (same count, same order, same bullet
  count per entry ± split/merge tolerance).

TAILORING LICENSE (what you CAN do):
- PARAPHRASE existing bullets to surface relevance — same underlying facts,
  reworded vocabulary that resonates with this job's language. Aggressive
  paraphrasing is encouraged as long as facts stay true.
- REORDER bullets within an entry (most relevant first).
- SPLIT a long bullet into several shorter ones when clearer; do not invent
  facts. MERGE two bullets into one when the result is tighter.
- REORDER the comma-separated skill lists inside the skills table rows so
  the skills most desired by this job come first (only real skills).

PROJECT SECTION (kind="proyectos" only) — FULL LICENSE over the entries list:
1. REORDER projects so the most relevant to this job appear first.
2. REMOVE a project entirely if it adds nothing to this application (omit its
   entry object). DO NOT invent projects — every project in your output must
   exist in the base CV.
3. FIRST bullet of each project MUST describe WHAT the project is / does /
   solves; subsequent bullets carry implementation details.
4. DESCRIPTOR field (the parenthetical above project bullets, e.g.
   "(Dashboard)", "(Agentic AI · RAG · Automatización)"): you MAY keep it as-is,
   MAY modify its content (only using real aspects evidenced in the bullets),
   or MAY set it to empty string "" if it adds no value. NEVER leave dangling
   empty parentheses "()".

SUMMARY FORMAT (HARD RULE):
The summary MUST follow this exact template:
  "En búsqueda de un puesto en <cat1> · <cat2> · <cat3>"
where each <cat> is a 2-4 word Spanish phrase, grounded in the candidate's
demonstrated experience, and NOT a verbatim copy from the job posting.
Good: "En búsqueda de un puesto en Ingeniería de Datos · Análisis · \
Automatización de Procesos"
Bad: "Estudiante de Economía con experiencia en…" (breaks the opener), or
echoing "Snowflake" / "dbt" verbatim from the posting.

OUTPUT FORMAT — return ONLY a single JSON object with this schema (no markdown,
no commentary). `enlaces` is intentionally absent; the orchestrator re-injects
those URLs post-rewrite:

{
  "summary": "En búsqueda de un puesto en <cat1> · <cat2> · <cat3>",
  "sections": [
    {
      "title": "<SECTION TITLE — copy from input>",
      "kind": "<educacion|experiencia|proyectos|habilidades — copy from input>",
      "entries": [
        {
          "titulo": "<immutable>", "fecha": "<immutable>",
          "subtitulo": "<immutable>", "descriptor": "<see PROJECT rules>",
          "bullets": ["<Spanish, MAY be reworded/split/merged>"]
        }
      ],
      "table": [["<label>", "<value>"]]
    }
  ]
}

Rules for the JSON:
- "habilidades": `entries` empty; `table` matches the input shape (same rows,
  same cells per row). First column (skill label) byte-for-byte; value cells
  MAY be reordered/rephrased using only skills present in the base CV.
- "educacion" / "experiencia": `table` empty; entry list mirrors the input
  (same count, same order, same bullet count ± split/merge).
- "proyectos": `table` empty; entry list MAY be shorter than base (if you
  removed irrelevant projects) and MAY be reordered. Never invent projects.
"""


def build_tailor_prompt(
    base_cv: CVProfile,
    job: JobInfo,
) -> tuple[str, str]:
    """Build (system, user) for the tailor pass."""
    base_for_llm = _strip_enlaces_for_llm(base_cv)
    user = (
        "BASE CV (the candidate's real, original résumé — everything you output "
        "must remain factually consistent with this; URLs are intentionally "
        "absent — DO NOT invent or modify any URL):\n\n"
        f"{json.dumps(base_for_llm, ensure_ascii=False, indent=2)}\n\n"
        "JOB TO ALIGN TOWARDS:\n\n"
        f"{job.to_markdown()}\n\n"
        "Produce the tailored CV JSON following every rule in the system "
        "prompt. Remember: NO new facts, NO verbatim copying from the job, "
        "same shape as base, and 'enlaces' arrays must be OMITTED."
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
   making the bullet read unnaturally. Severity "medium".
4. "incongruity": a claim that contradicts another claim in the tailored CV
   (e.g. role that didn't exist in base, mismatched dates). Severity "high".
5. "format": shape mismatch with the base CV — a renamed section title, \
   a skills-table row/cell count mismatch, or an added entry that did \
   NOT exist in the base CV. NOTE: for "proyectos" sections, having FEWER \
   entries than the base (projects removed for irrelevance) or a DIFFERENT \
   ORDER is ALLOWED and must NOT be flagged. For "experiencia" / \
   "educacion" sections, a different entry count or order IS a format issue. \
   Severity "high".
6. "summary_format": the summary line does NOT follow the required template \
   "En búsqueda de un puesto en <cat1> · <cat2> · <cat3>" — specifically, the \
   line must START with "En búsqueda de un puesto en " and contain at least \
   two " · " separators. Also flag if any of the <cat> phrases are verbatim \
   copied from the job posting or are NOT in Spanish. \
   Severity "high".
7. "immutable_changed": any project TITLE, dates, company names, role titles, \
   or university names that have been reworded. Severity "high". NOTE: the \
   "descriptor" field (parenthetical above project bullets) is EDITABLE for \
   the proyectos section and must NOT be flagged unless it was set to empty \
   parentheses "()" instead of empty string "", or unless its content invents \
   facts not evidenced by the base CV.
8. "bullet_order": for proyectos entries, the first bullet should describe WHAT \
   the project is/does (the "what & why" bullet), followed by implementation \
   details. Flag if the first bullet is a detail (e.g. dashboard) rather than \
   the project description. Severity "medium".
9. "length": the tailored CV is more than 25% longer or shorter than the base.
   Severity "low".
10. "language": output that is NOT Spanish, or that drops Spanish accents.
    Severity "high".
11. "url_tampered": any URL field was invented or modified by the LLM. The
    tailored JSON should NOT contain `enlaces` at all (the orchestrator
    re-injects them), so if you see one, flag it as a "high" issue.

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


def _strip_enlaces_from_tailored(tailored_json: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of the tailored JSON with all `enlaces` arrays
    removed from section entries.

    The orchestrator re-injects URLs from the base CV AFTER the tailor pass
    (see cv_rewriter._reinject_enlaces). Those reinjected `enlaces` are purely
    bookkeeping — they are NOT something the tailor LLM produced. Sending them
    to the evaluator would make it (correctly) flag `url_tampered` issues that
    are already handled deterministically. Stripping here gives the evaluator
    the LLM's actual output, so its attention stays on semantic issues.
    """
    import copy
    out = copy.deepcopy(tailored_json)
    for s in out.get("sections", []) or []:
        for e in s.get("entries", []) or []:
            if isinstance(e, dict):
                e.pop("enlaces", None)
    return out


def build_evaluator_prompt(
    base_cv: CVProfile,
    job: JobInfo,
    tailored_json: dict[str, Any],
) -> tuple[str, str]:
    # Strip reinjected enlaces so the evaluator judges what the tailor LLM
    # actually emitted, not the orchestrator's protected bookkeeping.
    tailored_view = _strip_enlaces_from_tailored(tailored_json)
    user_parts: list[str] = []
    user_parts.append("=== BASE CV (ground truth) ===")
    user_parts.append(json.dumps(_strip_enlaces_for_llm(base_cv), ensure_ascii=False, indent=2))
    user_parts.append("\n=== JOB POSTING ===")
    user_parts.append(job.to_markdown())
    user_parts.append("\n=== TAILORED CV (to be reviewed) ===")
    user_parts.append(json.dumps(tailored_view, ensure_ascii=False, indent=2))
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
    {
      "title": "...",
      "kind": "...",
      "entries": [{"titulo": "...", "fecha": "...",
        "subtitulo": "...", "descriptor": "...",
        "bullets": ["..."]}],
      "table": [["label", "value"]]
    }
  ]
}

Hard rules:
- The output MUST have the SAME shape as the tailored CV: same sections,
  same table dimensions. For "experiencia" / "educacion" sections, same
  entry count and bullet count. For "proyectos" sections, the same entry
  list as the tailored CV (do NOT add back projects the tailor removed).
  Only the text content changes, plus whatever the evaluator flagged.
- `enlaces` arrays must be OMITTED (URLs are protected and re-injected by
  the orchestrator). DO NOT invent or modify URLs.
- A "hallucination" fix means removing the fabricated claim or replacing it
  with what the base CV actually says — never invent a different alternative.
- A "verbatim_copy" fix means paraphrasing the phrase in the candidate's own
  voice. Never just synonym-swap one or two words.
- A "format" fix means restoring the original shape to match the base CV
  (for experiencias/educacion only; proyectos is flexible).
- DO NOT change anything the evaluator did not flag.
- Output MUST be in Spanish with proper accents.
- Return ONLY the JSON, no commentary.
"""


def build_repair_prompt(
    base_cv: CVProfile,
    tailored_json: dict[str, Any],
    issues: list[dict[str, Any]],
) -> tuple[str, str]:
    user_parts: list[str] = []
    user_parts.append("=== BASE CV (ground truth) ===")
    user_parts.append(json.dumps(_strip_enlaces_for_llm(base_cv), ensure_ascii=False, indent=2))
    user_parts.append("\n=== TAILORED CV (current, with issues) ===")
    user_parts.append(json.dumps(tailored_json, ensure_ascii=False, indent=2))
    user_parts.append("\n=== ISSUES TO FIX (only these) ===")
    user_parts.append(json.dumps(issues, ensure_ascii=False, indent=2))
    user_parts.append(
        "\nReturn the corrected tailored CV JSON following every rule. Keep the "
        "same shape as the input tailored CV. Omit 'enlaces' (URLs are protected)."
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