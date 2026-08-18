"""Tests for the tailor / evaluator / repair pipeline using a stubbed LLM client.

The new generic analysis.json schema:

  {
    "summary": "...",
    "sections": [
      {
        "id": "...", "title": "...",
        "type": "entry_block" | "simple_list" | "text_block",
        "reorderable": true,
        "entries": [{"heading", "subheading", "location", "dates",
                     "links": [{label, url}],    # protected — never sent to LLM
                     "bullets": [{"text", "tags"}]}],   # rewritable
        "items": [{"text", "tags"}],
        "text": "..."
      }
    ]
  }

The tailor pass hides `links` from the LLM (see prompts._strip_links_for_llm)
and re-injects the protected URLs back into the output afterwards.
_validate_shape is invoked BEFORE that re-injection, so it must flag any stray
`links`/`enlaces` arrays the LLM might mistakenly emit — and deterministically
drop empty bullets / empty-headed entries (no LLM involved).
"""

from __future__ import annotations

import json

from src.profile.cv_reader import (
    CVBullet,
    CVEntry,
    CVItem,
    CVProfile,
    CVSection,
    Enlace,
    PersonalInfo,
)
from src.tailor.cv_rewriter import (
    _reinject_links,
    _validate_shape,
    tailor_cv,
)
from src.tailor.evaluator import EvaluationResult, evaluate
from src.tailor.prompts import JobInfo, build_evaluator_prompt, build_tailor_prompt
from src.tailor.repair import RepairResult, repair_cv
from tests.test_helpers_llm import StubLLMClient, llm_response


def _base_cv() -> CVProfile:
    return CVProfile(
        personal_info=PersonalInfo(
            name="MARÍA FERNANDA ROJAS",
            email="maria@example.com",
            links=[
                Enlace(label="Sitio", url="https://sitio.example.com"),
            ],
        ),
        summary="Perfil orientada a productos digitales y automatización.",
        sections=[
            CVSection(
                id="perfil", title="Perfil Profesional", type="text_block",
                text="Ingeniera de Sistemas con experiencia en automatización.",
            ),
            CVSection(
                id="experiencia", title="Experiencia Laboral", type="entry_block",
                reorderable=False,
                entries=[
                    CVEntry(
                        heading="Analista — TechFlow Perú",
                        dates="Mar 2023 – Actualidad",
                        bullets=[
                            CVBullet(text="Automaticé el registro contable en SAP.", tags=["sap"]),
                            CVBullet(text="Diseñé dashboards en Power BI.", tags=["powerbi"]),
                        ],
                    ),
                    CVEntry(
                        heading="Practicante — DataCorp",
                        dates="Ene 2022 – Feb 2023",
                        bullets=[
                            CVBullet(text="Elaboré reportes con SQL y Excel.", tags=["sql", "excel"]),
                        ],
                    ),
                ],
            ),
            CVSection(
                id="proyectos", title="Proyectos", type="entry_block",
                reorderable=True,
                entries=[
                    CVEntry(
                        heading="BOT de Conciliación con IA",
                        dates="2025",
                        links=[
                            Enlace(label="Dashboard", url="https://example-dashboard.example.com/"),
                        ],
                        bullets=[
                            CVBullet(text="Automatiza la conciliación bancaria con IA.", tags=["ia"]),
                            CVBullet(text="Dashboard en Streamlit.", tags=["streamlit"]),
                        ],
                    ),
                    CVEntry(
                        heading="KAYLA — Recordatorios de salud",
                        dates="2025",
                        links=[
                            Enlace(label="Landing Page", url="https://example-landing.example.com/"),
                            Enlace(label="Dashboard", url="https://example-dashboard-2.example.com/"),
                        ],
                        bullets=[
                            CVBullet(text="Recuerda citas y medicamentos.", tags=["salud"]),
                            CVBullet(text="Bot de Telegram + Google Sheets.", tags=["telegram"]),
                        ],
                    ),
                ],
            ),
            CVSection(
                id="habilidades", title="Habilidades & Herramientas", type="simple_list",
                items=[
                    CVItem(text="Python (Pandas, Streamlit)", tags=["python"]),
                    CVItem(text="SQL y Excel", tags=["sql"]),
                ],
            ),
        ],
        raw_text="MARÍA FERNANDA\n...",
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


def _valid_tailored() -> dict:
    return {
        "summary": "Perfil orientada a automatización y análisis de datos.",
        "sections": [
            {
                "id": "perfil", "title": "Perfil Profesional", "type": "text_block",
                "reorderable": False, "text": "Ingeniera con experiencia en automatización y datos.",
            },
            {
                "id": "experiencia", "title": "Experiencia Laboral", "type": "entry_block",
                "reorderable": False,
                "entries": [
                    {
                        "heading": "Analista — TechFlow Perú", "subheading": "",
                        "location": "", "dates": "Mar 2023 – Actualidad",
                        "bullets": [
                            {"text": "Automaticé el registro contable en SAP.", "tags": ["sap"]},
                            {"text": "Diseñé dashboards en Power BI.", "tags": ["powerbi"]},
                        ],
                    },
                    {
                        "heading": "Practicante — DataCorp", "subheading": "",
                        "location": "", "dates": "Ene 2022 – Feb 2023",
                        "bullets": [
                            {"text": "Elaboré reportes con SQL y Excel.", "tags": ["sql", "excel"]},
                        ],
                    },
                ],
            },
            {
                "id": "proyectos", "title": "Proyectos", "type": "entry_block",
                "reorderable": True,
                "entries": [
                    {
                        "heading": "BOT de Conciliación con IA", "subheading": "",
                        "location": "", "dates": "2025",
                        "bullets": [
                            {"text": "Automatiza la conciliación bancaria con IA.", "tags": ["ia"]},
                            {"text": "Dashboard en Streamlit.", "tags": ["streamlit"]},
                        ],
                    },
                    {
                        "heading": "KAYLA — Recordatorios de salud", "subheading": "",
                        "location": "", "dates": "2025",
                        "bullets": [
                            {"text": "Recuerda citas y medicamentos.", "tags": ["salud"]},
                            {"text": "Bot de Telegram + Google Sheets.", "tags": ["telegram"]},
                        ],
                    },
                ],
            },
            {
                "id": "habilidades", "title": "Habilidades & Herramientas", "type": "simple_list",
                "reorderable": False,
                "items": [
                    {"text": "Python (Pandas, Streamlit)", "tags": ["python"]},
                    {"text": "SQL y Excel", "tags": ["sql"]},
                ],
            },
        ],
    }


def _valid_job_summary() -> str:
    return json.dumps({
        "requisitos_duros": ["SQL avanzado", "Python"],
        "skills_deseadas": ["Snowflake", "dbt"],
        "funciones_clave": ["Construir pipelines ETL en cloud"],
    }, ensure_ascii=False)


# ---------- _validate_shape tests ----------


class TestValidateShape:
    def test_valid_output(self):
        base = _base_cv()
        warnings = _validate_shape(_valid_tailored(), base)
        assert warnings == [], f"unexpected warnings: {warnings}"

    def test_missing_summary(self):
        tailored = _valid_tailored()
        del tailored["summary"]
        warnings = _validate_shape(tailored, _base_cv())
        assert any("summary" in w for w in warnings)

    def test_section_titles_mismatch(self):
        tailored = _valid_tailored()
        tailored["sections"][0]["title"] = "WRONG TITLE"
        warnings = _validate_shape(tailored, _base_cv())
        assert any("titles" in w for w in warnings)

    def test_type_mismatch(self):
        tailored = _valid_tailored()
        tailored["sections"][0]["type"] = "simple_list"
        warnings = _validate_shape(tailored, _base_cv())
        assert any("type mismatch" in w for w in warnings)

    def test_non_reorderable_entry_count_mismatch_warns(self):
        tailored = _valid_tailored()
        # Drop one experiencia entry (non-reorderable) → warning.
        tailored["sections"][1]["entries"] = tailored["sections"][1]["entries"][:1]
        warnings = _validate_shape(tailored, _base_cv())
        assert any("entry count" in w for w in warnings)

    def test_reorderable_removed_entry_no_warning(self):
        """reorderable: true + an entry removed → NO warning (FASE 7.3)."""
        tailored = _valid_tailored()
        tailored["sections"][2]["entries"] = tailored["sections"][2]["entries"][:1]
        warnings = _validate_shape(tailored, _base_cv())
        assert not any("entry count" in w for w in warnings)

    def test_reorderable_invented_entry_warns(self):
        tailored = _valid_tailored()
        tailored["sections"][2]["entries"].append({
            "heading": "Invented Project", "subheading": "", "location": "",
            "dates": "2099", "bullets": [{"text": "fake", "tags": []}],
        })
        warnings = _validate_shape(tailored, _base_cv())
        assert any("not found in base" in w for w in warnings)

    def test_bullet_count_mismatch_warns(self):
        tailored = _valid_tailored()
        # One extra bullet in a non-reorderable entry → warning.
        tailored["sections"][1]["entries"][0]["bullets"].append(
            {"text": "extra", "tags": []}
        )
        warnings = _validate_shape(tailored, _base_cv())
        assert any("bullet count" in w for w in warnings)

    def test_immutable_field_drift_is_flagged(self):
        tailored = _valid_tailored()
        tailored["sections"][1]["entries"][0]["dates"] = "WRONG DATE"
        warnings = _validate_shape(tailored, _base_cv())
        assert any("immutable field" in w and "dates" in w for w in warnings)

    def test_llm_including_links_is_flagged(self):
        tailored = _valid_tailored()
        tailored["sections"][2]["entries"][0]["links"] = [
            {"label": "evil", "url": "evil://tampered"}
        ]
        warnings = _validate_shape(tailored, _base_cv())
        assert any("'links'" in w and "must be omitted" in w for w in warnings)

    def test_no_summary_template_rule(self):
        """The 'En búsqueda de un puesto en...' hard rule is GONE. Any summary
        is structurally valid (FASE 3.5)."""
        tailored = _valid_tailored()
        tailored["summary"] = "Estudiante con experiencia en análisis de datos"
        warnings = _validate_shape(tailored, _base_cv())
        assert not any("summary" in w for w in warnings)

    def test_empty_bullet_is_dropped_deterministically(self):
        """A bullet with 'text': '-' or '' is removed with a warning, with NO
        LLM call (FASE 7.10 / 3.5)."""
        tailored = _valid_tailored()
        tailored["sections"][1]["entries"][0]["bullets"].append({"text": "-", "tags": []})
        tailored["sections"][1]["entries"][0]["bullets"].append({"text": "  •  ", "tags": []})
        warnings = _validate_shape(tailored, _base_cv())
        assert any("descartado" in w for w in warnings)
        remaining = [b["text"] for b in tailored["sections"][1]["entries"][0]["bullets"]]
        assert remaining == [
            "Automaticé el registro contable en SAP.",
            "Diseñé dashboards en Power BI.",
        ]

    def test_empty_heading_entry_is_dropped(self):
        tailored = _valid_tailored()
        tailored["sections"][2]["entries"][0]["heading"] = "  "
        warnings = _validate_shape(tailored, _base_cv())
        assert any("heading vacío" in w for w in warnings)
        headings = [e["heading"] for e in tailored["sections"][2]["entries"]]
        assert headings == ["KAYLA — Recordatorios de salud"]


# ---------- _reinject_links tests ----------


class TestReinjectLinks:
    def test_reinjects_protected_urls_from_base(self):
        tailored = _valid_tailored()
        # LLM "emitted" a fake links array; it must be overwritten.
        tailored["sections"][2]["entries"][0]["links"] = [{"label": "fake", "url": "evil"}]
        _reinject_links(tailored, _base_cv())
        pr0 = tailored["sections"][2]["entries"][0]
        assert pr0["links"] == [
            {"label": "Dashboard", "url": "https://example-dashboard.example.com/"}
        ]
        pr1 = tailored["sections"][2]["entries"][1]
        assert pr1["links"] == [
            {"label": "Landing Page", "url": "https://example-landing.example.com/"},
            {"label": "Dashboard", "url": "https://example-dashboard-2.example.com/"},
        ]
        # entry_block sections without base links get no links key
        assert "links" not in tailored["sections"][1]["entries"][0]

    def test_reinject_works_with_reordered_entries(self):
        tailored = _valid_tailored()
        tailored["sections"][2]["entries"].reverse()  # KAYLA first now
        _reinject_links(tailored, _base_cv())
        pr0 = tailored["sections"][2]["entries"][0]
        assert pr0["links"] == [
            {"label": "Landing Page", "url": "https://example-landing.example.com/"},
            {"label": "Dashboard", "url": "https://example-dashboard-2.example.com/"},
        ]
        pr1 = tailored["sections"][2]["entries"][1]
        assert pr1["links"] == [
            {"label": "Dashboard", "url": "https://example-dashboard.example.com/"}
        ]

    def test_reinject_skips_non_entry_sections(self):
        tailored = _valid_tailored()
        _reinject_links(tailored, _base_cv())
        assert "links" not in tailored["sections"][0]   # text_block
        assert "links" not in tailored["sections"][3]   # simple_list


# ---------- tailor_cv tests ----------


class TestTailorCV:
    def test_routes_correct_payload_through_stub(self):
        base = _base_cv()
        canned = _valid_tailored()
        stub = StubLLMClient([llm_response(_json(canned))])
        result = tailor_cv(stub, base, _job(), model="glm-5.2")
        assert len(stub.calls) == 1
        kwargs = stub.calls[0]
        assert kwargs["model"] == "glm-5.2"
        assert kwargs["json_mode"] is True
        assert result.tailored_json["summary"] == canned["summary"]
        assert result.shape_warnings == []

    def test_urls_do_not_leak_to_llm_prompt(self):
        base = _base_cv()
        stub = StubLLMClient([llm_response(_json(_valid_tailored()))])
        tailor_cv(stub, base, _job(), model="glm-5.2")
        user = stub.calls[0]["user"]
        assert "https://example-dashboard.example.com/" not in user
        assert "https://example-landing.example.com/" not in user
        assert "https://sitio.example.com" not in user
        # Headings ARE sent (visible, immutable):
        assert "BOT de Conciliación con IA" in user

    def test_links_reinjected_after_response(self):
        stub = StubLLMClient([llm_response(_json(_valid_tailored()))])
        result = tailor_cv(stub, _base_cv(), _job(), model="glm-5.2")
        pr0 = result.tailored_json["sections"][2]["entries"][0]
        assert pr0["links"] == [
            {"label": "Dashboard", "url": "https://example-dashboard.example.com/"}
        ]

    def test_handles_markdown_fenced_json(self):
        fenced = f"```json\n{_json(_valid_tailored())}\n```"
        stub = StubLLMClient([llm_response(fenced)])
        result = tailor_cv(stub, _base_cv(), _job())
        assert result.tailored_json["summary"].startswith("Perfil orientada")

    def test_unwraps_backend_junk_envelope(self):
        """Some backends wrap the real payload: {" .json": "<json string>"}."""
        inner = _json(_valid_tailored())
        wrapped = _json({".json": inner})
        stub = StubLLMClient([llm_response(wrapped)])
        result = tailor_cv(stub, _base_cv(), _job())
        assert result.tailored_json["summary"].startswith("Perfil orientada")
        assert result.shape_warnings == []

    def test_ignores_junk_keys_alongside_payload(self):
        """Stray junk keys (e.g. `/**/`) must not break parsing/validation."""
        valid = json.loads(_json(_valid_tailored()))
        valid["/**/"] = "json"
        stub = StubLLMClient([llm_response(_json(valid))])
        result = tailor_cv(stub, _base_cv(), _job())
        assert result.tailored_json["summary"].startswith("Perfil orientada")
        assert result.shape_warnings == []

    def test_normalizes_leading_slash_keys(self):
        valid = _valid_tailored()
        slash_keys = json.loads(_json(valid))

        def slashify(value):
            if isinstance(value, dict):
                return {"/" + key: slashify(item) for key, item in value.items()}
            if isinstance(value, list):
                return [slashify(item) for item in value]
            return value

        stub = StubLLMClient([llm_response(_json(slashify(slash_keys)))])
        result = tailor_cv(stub, _base_cv(), _job())
        assert result.tailored_json["summary"].startswith("Perfil orientada")
        assert result.shape_warnings == []

    def test_retries_empty_provider_response(self):
        stub = StubLLMClient([
            llm_response("{}"),
            llm_response(_json(_valid_tailored())),
        ])
        result = tailor_cv(stub, _base_cv(), _job())
        assert len(stub.calls) == 2
        assert result.tailored_json["sections"]

    def test_records_shape_warnings_on_bad_output(self):
        bad = _json({
            "summary": "x",
            "sections": [{"title": "WRONG", "type": "x", "entries": [], "items": [], "text": ""}],
        })
        stub = StubLLMClient([llm_response(bad)])
        result = tailor_cv(stub, _base_cv(), _job())
        assert result.shape_warnings

    def test_user_preferences_appear_when_present(self):
        stub = StubLLMClient([llm_response(_json(_valid_tailored()))])
        tailor_cv(stub, _base_cv(), _job(), user_preferences="Resumen con 'En búsqueda de un puesto en'")
        user = stub.calls[0]["user"]
        assert "INSTRUCCIONES PERSONALES DEL CANDIDATO" in user
        assert "En búsqueda de un puesto en" in user

    def test_user_preferences_absent_when_empty(self):
        """FASE 7.6: the personal-instructions block must NOT appear when
        preferences are empty."""
        stub = StubLLMClient([llm_response(_json(_valid_tailored()))])
        tailor_cv(stub, _base_cv(), _job(), user_preferences="")
        user = stub.calls[0]["user"]
        assert "INSTRUCCIONES PERSONALES DEL CANDIDATO" not in user


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
            "overall_verdict": "needs_repair", "summary": "hallucination found",
        }
        stub = StubLLMClient([llm_response(_json(canned))])
        result = evaluate(stub, _base_cv(), _job(), {"summary": "x", "sections": []})
        assert result.needs_repair is True
        assert result.issues[0]["severity"] == "high"

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
        stub = StubLLMClient([llm_response(_json(_valid_tailored()))])
        issues = [{"id": "1", "type": "verbatim_copy", "severity": "high",
                   "quote": "pipelines ETL en cloud"}]
        result = repair_cv(stub, _base_cv(), {"summary": "x", "sections": []}, issues)
        assert isinstance(result, RepairResult)
        assert result.repaired_json["summary"].startswith("Perfil orientada")
        assert stub.calls[0]["model"]

    def test_validation_warnings_propagate(self):
        bad = _json({"summary": "x", "sections": [{"title": "WRONG", "type": "",
                                                   "entries": [], "items": [], "text": ""}]})
        stub = StubLLMClient([llm_response(bad)])
        result = repair_cv(stub, _base_cv(), {"summary": "x", "sections": []}, [])
        assert result.shape_warnings


# ---------- prompts tests ----------


class TestPrompts:
    def test_tailor_clearly_mentions_base_cv(self):
        base = _base_cv()
        sys_p, user_p = build_tailor_prompt(base, _job())
        assert "BASE CV" in user_p
        assert "JOB TO ALIGN TOWARDS" in user_p
        assert base.personal_info.name in user_p
        assert "Snowflake" in user_p  # job summary text

    def test_system_prompt_is_generic_no_4_kind_enum(self):
        """FASE 3.3: the system prompts must NOT reference the old 4-kind enum
        nor the 'En búsqueda' template."""
        sys_p, _ = build_tailor_prompt(_base_cv(), _job())
        assert "educacion" not in sys_p and "proyectos" not in sys_p
        assert "habilidades" not in sys_p and "experiencia" not in sys_p
        assert "En búsqueda de un puesto en" not in sys_p
        # The generic vocabulary IS present:
        assert "entry_block" in sys_p
        assert "reorderable" in sys_p

    def test_tailor_system_says_urls_protected(self):
        sys_p, _ = build_tailor_prompt(_base_cv(), _job())
        assert "links" in sys_p.lower()
        assert "protected" in sys_p.lower() or "intentionally" in sys_p.lower()

    def test_evaluator_includes_all_three(self):
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
    return json.dumps(obj, ensure_ascii=False)
