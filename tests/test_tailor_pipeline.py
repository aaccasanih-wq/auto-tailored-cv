"""Tests for the tailor / evaluator / repair pipeline using a stubbed LLM client."""

from __future__ import annotations

import pytest

from src.profile.cv_reader import CVProfile, CVSection
from src.tailor.cv_rewriter import tailor_cv, _validate_shape, TailorResult
from src.tailor.evaluator import evaluate, EvaluationResult
from src.tailor.repair import repair_cv, RepairResult
from src.tailor.prompts import JobInfo

from tests.test_helpers_llm import StubLLMClient, llm_response


def _base_cv() -> CVProfile:
    return CVProfile(
        name="ALEX SAMPLE CANDIDATE",
        contact="555 0100 | email@example.com",
        summary="En búsqueda de un puesto en Data Science · Análisis · Visualización",
        sections=[
            CVSection(
                title="EDUCACIÓN",
                paragraphs=["Lic. en Economía | 2021 – 2026"],
                tables=[[["Example University", "2021 – 2026"]]],
            ),
            CVSection(
                title="EXPERIENCIA LABORAL",
                paragraphs=["Automaticé validación de facturas en Excel."],
                tables=[[["ExampleCorp — Intern", "Nov 2024 – Feb 2025"]]],
            ),
            CVSection(
                title="HABILIDADES & HERRAMIENTAS",
                paragraphs=[],
                tables=[[["Python", "Jupyter, Pandas, NumPy"], ["Excel", "SAP"]]],
            ),
        ],
        raw_text="ALEX\n...",
    )


def _job() -> JobInfo:
    return JobInfo(
        title="Data Engineer",
        company="Acme",
        location="Lima, Peru",
        description=(
            "Buscamos Data Engineer con experiencia en pipelines ETL en cloud, "
            "Snowflake y dbt. Liderar equipos ágiles. Excel intermedio. SQL avanzado."
        ),
    )


# ---------- cv_rewriter tests ----------


class TestValidateShape:
    def test_valid_output(self):
        base = _base_cv()
        tailored = {
            "summary": "En búsqueda de un puesto en Data Engineering · Pipelines · Cloud",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["Lic. en Economía | 2021 – 2026"],
                 "tables": [[["Example University", "2021 – 2026"]]]},
                {"title": "EXPERIENCIA LABORAL",
                 "paragraphs": ["Automaticé validación de facturas en Excel y SAP."],
                 "tables": [[["ExampleCorp — Intern", "Nov 2024 – Feb 2025"]]]},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [],
                 "tables": [[["Python", "Pandas, NumPy, Jupyter"], ["Excel", "SAP"]]]},
            ],
        }
        assert _validate_shape(tailored, base) == []

    def test_missing_summary(self):
        base = _base_cv()
        tailored = {"sections": []}
        warnings = _validate_shape(tailored, base)
        assert any("summary" in w for w in warnings)

    def test_section_mismatch(self):
        base = _base_cv()
        tailored = {"summary": "x", "sections": [{"title": "WRONG", "paragraphs": [], "tables": []}]}
        warnings = _validate_shape(tailored, base)
        assert any("section titles" in w for w in warnings)

    def test_paragraph_count_mismatch(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["a", "b"], "tables": [[["c", "d"]]]},
                {"title": "EXPERIENCIA LABORAL", "paragraphs": ["a"], "tables": [[["c", "d"]]]},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [], "tables": [[[["e"]]]]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("EDUCACIÓN" in w and "paragraph" in w for w in warnings)

    def test_table_dimension_mismatch(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["a"], "tables": [[["only one cell"]]]},
                {"title": "EXPERIENCIA LABORAL", "paragraphs": ["a"], "tables": [[["c", "d"]]]},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [], "tables": [[["a", "b"]]]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("col count" in w or "cell count" in w or "EDUCACIÓN" in w for w in warnings)


class TestSummaryFormatValidation:
    """The base CV summary starts with 'En búsqueda de un puesto en ...'.
    A tailored summary MUST preserve that template (enforced in code)."""

    def _base_cv_harvard(_):  # noqa
        return CVProfile(
            name="ALEX",
            contact="email",
            summary="En búsqueda de un puesto en X · Y · Z",
            sections=[
                CVSection(title="EDUCACIÓN", paragraphs=["a"], tables=[[["u", "y"]]]),
            ],
            raw_text="...",
        )

    def test_keeps_template_no_warning(self):
        base = CVProfile(
            name="x", contact="y",
            summary="En búsqueda de un puesto en Digital Products · Análisis de Datos · Transformación Digital",
            sections=[], raw_text="",
        )
        tailored = {
            "summary": "En búsqueda de un puesto en Ingeniería de Datos · Reportes · Power BI",
            "sections": [],
        }
        warnings = _validate_shape(tailored, base)
        assert not any("summary" in w for w in warnings)

    def test_breaks_template_warns(self):
        base = CVProfile(
            name="x", contact="y",
            summary="En búsqueda de un puesto en Digital Products · Análisis · Transf",
            sections=[], raw_text="",
        )
        tailored = {
            "summary": "Estudiante de Economía con experiencia en análisis de datos",
            "sections": [],
        }
        warnings = _validate_shape(tailored, base)
        assert any("summary must start" in w for w in warnings)

    def test_missing_dots_warns(self):
        base = CVProfile(
            name="x", contact="y",
            summary="En búsqueda de un puesto en X · Y · Z",
            sections=[], raw_text="",
        )
        tailored = {
            "summary": "En búsqueda de un puesto en Data Engineering",  # 0 separators
            "sections": [],
        }
        warnings = _validate_shape(tailored, base)
        assert any("separators" in w for w in warnings)

    def test_no_base_summary_no_warning(self):
        # If base CV had NO "En búsqueda..." opening, we don't enforce the template.
        base = CVProfile(
            name="x", contact="y",
            summary="Data scientist with 5 years of experience",
            sections=[], raw_text="",
        )
        tailored = {"summary": "anything goes here", "sections": []}
        warnings = _validate_shape(tailored, base)
        assert not any("summary" in w for w in warnings)


class TestTailorCV:
    def test_routes_correct_payload_through_stub(self):
        base = _base_cv()
        canned = {
            "summary": "En búsqueda de un puesto en Data Engineering · Pipelines · Cloud",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["Lic. en Economía | 2021 – 2026"],
                 "tables": [[["Example University", "2021 – 2026"]]]},
                {"title": "EXPERIENCIA LABORAL",
                 "paragraphs": ["Automaticé la validación de facturas en Excel y SAP."],
                 "tables": [[["ExampleCorp — Intern", "Nov 2024 – Feb 2025"]]]},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [],
                 "tables": [[["Python", "Pandas, NumPy, Jupyter"], ["Excel", "SAP"]]]},
            ],
        }
        stub = StubLLMClient([llm_response(_json(canned))])
        result = tailor_cv(stub, base, _job(), model="glm-5.2")
        # One chat call, with our system + user prompts
        assert len(stub.calls) == 1
        kwargs = stub.calls[0]
        assert kwargs["model"] == "glm-5.2"
        assert kwargs["json_mode"] is True
        assert "TAILORED" not in kwargs["user"]  # placeholder check
        # JSON shape preserved
        assert result.tailored_json["summary"] == canned["summary"]
        assert result.shape_warnings == []

    def test_handles_markdown_fenced_json(self):
        base = _base_cv()
        valid = _json({
            "summary": "x",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["Lic. en Economía | 2021 – 2026"],
                 "tables": [[["Example University", "2021 – 2026"]]]},
                {"title": "EXPERIENCIA LABORAL", "paragraphs": ["a"],
                 "tables": [[["ExampleCorp — Intern", "Nov 2024 – Feb 2025"]]]},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [],
                 "tables": [[["Python", "Pandas, NumPy, Jupyter"], ["Excel", "SAP"]]]},
            ],
        })
        fenced = f"```json\n{valid}\n```"
        stub = StubLLMClient([llm_response(fenced)])
        result = tailor_cv(stub, base, _job())
        assert result.tailored_json["summary"] == "x"

    def test_records_shape_warnings_on_bad_output(self):
        base = _base_cv()
        bad = _json({
            "summary": "x",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["a", "b"], "tables": []},
                {"title": "EXPERIENCIA LABORAL", "paragraphs": ["a"], "tables": []},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [], "tables": []},
            ],
        })
        stub = StubLLMClient([llm_response(bad)])
        result = tailor_cv(stub, base, _job())
        assert result.shape_warnings  # non-empty
        # The user-facing tailoring is preserved (we keep the JSON even with warnings)
        assert result.tailored_json["summary"] == "x"


# ---------- evaluator tests ----------


class TestEvaluator:
    def test_passes_when_no_issues(self):
        canned = {"issues": [], "overall_verdict": "pass", "summary": "ok"}
        stub = StubLLMClient([llm_response(_json(canned))])
        result = evaluate(stub, _base_cv(), _job(), {"summary": "x", "sections": []})
        assert isinstance(result, EvaluationResult)
        assert result.verdict == "pass"
        assert result.issues == []
        assert not result.needs_repair

    def test_marks_needs_repair_on_high_issue(self):
        canned = {
            "issues": [{
                "id": "1", "type": "hallucination", "severity": "HIGH",
                "quote": "Snowflake", "base_quote": None,
                "explanation": "Snowflake not in base CV", "suggested_fix": "remove it"
            }],
            "overall_verdict": "needs_repair",
            "summary": "hallucination found",
        }
        stub = StubLLMClient([llm_response(_json(canned))])
        result = evaluate(stub, _base_cv(), _job(), {"summary": "x", "sections": []})
        assert result.needs_repair is True
        assert result.issues[0]["severity"] == "high"  # normalized to lowercase

    def test_invalid_json_is_fail_verdict(self):
        stub = StubLLMClient([llm_response("not json")])
        result = evaluate(stub, _base_cv(), _job(), {"summary": "x", "sections": []})
        assert result.verdict == "fail"
        assert result.issues[0]["severity"] == "high"

    def test_unknown_verdict_normalized(self):
        canned = {"issues": [], "overall_verdict": "weird", "summary": "x"}
        stub = StubLLMClient([llm_response(_json(canned))])
        result = evaluate(stub, _base_cv(), _job(), {"summary": "x", "sections": []})
        assert result.verdict == "needs_repair"


# ---------- repair tests ----------


class TestRepair:
    def test_routes_issues_to_prompt(self):
        valid = _json({
            "summary": "x fixed",
            "sections": [
                {"title": "EDUCACIÓN", "paragraphs": ["Lic. en Economía | 2021 – 2026"],
                 "tables": [[["Example University", "2021 – 2026"]]]},
                {"title": "EXPERIENCIA LABORAL", "paragraphs": ["a"],
                 "tables": [[["ExampleCorp — Intern", "Nov 2024 – Feb 2025"]]]},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [],
                 "tables": [[["Python", "Pandas, NumPy, Jupyter"], ["Excel", "SAP"]]]},
            ],
        })
        stub = StubLLMClient([llm_response(valid)])
        issues = [{"id": "1", "type": "verbatim_copy", "severity": "high", "quote": "pipelines ETL en cloud"}]
        result = repair_cv(stub, _base_cv(), {"summary": "x", "sections": []}, issues)
        assert isinstance(result, RepairResult)
        assert result.repaired_json["summary"] == "x fixed"
        assert stub.calls[0]["model"]  # populated from settings default

    def test_validation_warnings_propagate(self):
        bad = _json({
            "summary": "x",
            "sections": [
                {"title": "WRONG", "paragraphs": [], "tables": []},
                {"title": "EXPERIENCIA LABORAL", "paragraphs": [], "tables": []},
                {"title": "HABILIDADES & HERRAMIENTAS", "paragraphs": [], "tables": []},
            ],
        })
        stub = StubLLMClient([llm_response(bad)])
        result = repair_cv(stub, _base_cv(), {"summary": "x", "sections": []}, [])
        assert result.shape_warnings  # alerted


# ---------- prompts tests ----------


class TestPrompts:
    def test_tailor_clearly_mentions_base_cv(self):
        from src.tailor.prompts import build_tailor_prompt
        base = _base_cv()
        sys_p, user_p = build_tailor_prompt(base, _job())
        assert "BASE CV" in user_p
        assert "JOB TO ALIGN TOWARDS" in user_p
        assert base.name in user_p
        assert "Snowflake" in user_p  # job desc text

    def test_evaluator_includes_all_three(self):
        from src.tailor.prompts import build_evaluator_prompt
        sys_p, user_p = build_evaluator_prompt(_base_cv(), _job(),
                                                {"summary": "x", "sections": []})
        assert "BASE CV" in user_p
        assert "JOB POSTING" in user_p
        assert "TAILORED CV" in user_p

    def test_repair_passes_issues(self):
        from src.tailor.prompts import build_repair_prompt
        issues = [{"id": "1", "type": "hallucination", "severity": "high"}]
        sys_p, user_p = build_repair_prompt(_base_cv(),
                                            {"summary": "x", "sections": []}, issues)
        assert "ISSUES TO FIX" in user_p
        assert "hallucination" in user_p


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)