"""Tests for the tailor / evaluator / repair pipeline using a stubbed LLM client.

The new analysis.json schema uses typed entries with protected `enlaces`:

  {
    "summary": "...",
    "sections": [
      {
        "title": "...",
        "kind": "educacion" | "experiencia" | "proyectos" | "habilidades",
        "entries": [
          {
            "titulo": "...", "fecha": "...",
            "subtitulo": "...", "descriptor": "...",
            "enlaces": [{texto, url}],   # protected — never sent to LLM
            "bullets": ["..."]          # rewritable
          }
        ],
        "table": [["label", "value"], ...]   # habilidades only
      }
    ]
  }

The tailor pass hides `enlaces` from the LLM (see prompts._strip_enlaces_for_llm)
and re-injects the protected URLs back into the output afterwards.
_validate_shape is invoked BEFORE that re-injection, so it must flag any stray
`enlaces` arrays the LLM might mistakenly emit.
"""

from __future__ import annotations

import json

from src.profile.cv_reader import CVEntry, CVProfile, CVSection, Enlace
from src.tailor.cv_rewriter import (
    _reinject_enlaces,
    _validate_shape,
    tailor_cv,
)
from src.tailor.evaluator import EvaluationResult, evaluate
from src.tailor.prompts import JobInfo, build_evaluator_prompt, build_tailor_prompt
from src.tailor.repair import RepairResult, repair_cv
from tests.test_helpers_llm import StubLLMClient, llm_response


def _base_cv() -> CVProfile:
    return CVProfile(
        name="ALEX SAMPLE CANDIDATE",
        contact="email@example.com",
        contact_enlaces=[],
        summary="En búsqueda de un puesto en Data Science · Análisis · Visualización",
        sections=[
            CVSection(
                title="Educación",
                kind="educacion",
                entries=[
                    CVEntry(
                        titulo="Example University — Lima",
                        fecha="2021 – 2026",
                        subtitulo="Lic. en Economía | En proceso",
                    )
                ],
            ),
            CVSection(
                title="Experiencia Laboral",
                kind="experiencia",
                entries=[
                    CVEntry(
                        titulo="ExampleCorp — Intern",
                        fecha="Nov 2024 – Feb 2025",
                        bullets=["Automaticé la validación de facturas en Excel."],
                    ),
                    CVEntry(
                        titulo="OtherCorp — Analyst",
                        fecha="Dic 2023 – Abr 2024",
                        bullets=["Optimicé procesos internos de pagos."],
                    ),
                ],
            ),
            CVSection(
                title="Proyectos",
                kind="proyectos",
                entries=[
                    CVEntry(
                        titulo="Rastreador de Gastos Automatizado con IA",
                        fecha="May 2026",
                        descriptor="(Dashboard)",
                        enlaces=[Enlace(texto="Dashboard",
                                         url="https://example-dashboard.example.com/")],
                        bullets=["Desarrollé una automatización bancaria.",
                                  "Desarrollé un dashboard Streamlit."],
                    ),
                    CVEntry(
                        titulo="KAYLA",
                        fecha="Jun 2026",
                        descriptor="(Landing Page · Dashboard)",
                        enlaces=[
                            Enlace(texto="Landing Page",
                                    url="https://example-landing.example.com/"),
                            Enlace(texto="Dashboard",
                                    url="https://example-dashboard-2.example.com/"),
                        ],
                        bullets=["Diseñé un sistema de recordatorios médicos.",
                                  "Construí un dashboard para monitorear.",
                        ],
                    ),
                ],
            ),
            CVSection(
                title="Habilidades & Herramientas",
                kind="habilidades",
                table=[
                    ["Python", "Pandas, NumPy, Jupyter"],
                    ["Excel", "SAP"],
                ],
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


# ---------- _validate_shape tests ----------


class TestValidateShape:
    def test_valid_output(self):
        base = _base_cv()
        tailored = {
            "summary": "En búsqueda de un puesto en Ingeniería de Datos · Pipelines · Cloud",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "Example University — Lima", "fecha": "2021 – 2026",
                     "subtitulo": "Lic. en Economía | En proceso",
                     "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "ExampleCorp — Intern", "fecha": "Nov 2024 – Feb 2025",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["Automaté la validación de facturas en Excel y SAP."]},
                    {"titulo": "OtherCorp — Analyst", "fecha": "Dic 2023 – Abr 2024",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["Optimicé procesos internos de validación."]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "Rastreador de Gastos Automatizado con IA", "fecha": "May 2026",
                     "subtitulo": "", "descriptor": "(Dashboard)",
                     "bullets": ["Automaté una herramienta de análisis bancario.",
                                  "Construí un dashboard Streamlit para análisis."]},
                    {"titulo": "KAYLA", "fecha": "Jun 2026",
                     "subtitulo": "", "descriptor": "(Landing Page · Dashboard)",
                     "bullets": ["Diseñé recordatorios médicos automatizados.",
                                  "Construí un dashboard de monitoreo."]},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [
                     ["Python", "Pandas, NumPy, Streamlit, Selenium"],
                     ["Excel", "SAP"],
                 ]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert warnings == [], f"unexpected warnings: {warnings}"

    def test_missing_summary(self):
        base = _base_cv()
        tailored = {"sections": []}
        warnings = _validate_shape(tailored, base)
        assert any("summary" in w for w in warnings)

    def test_section_mismatch(self):
        base = _base_cv()
        tailored = {"summary": "x",
                    "sections": [{"title": "WRONG", "kind": "x",
                                  "entries": [], "table": []}]}
        warnings = _validate_shape(tailored, base)
        assert any("section titles" in w for w in warnings)

    def test_kind_mismatch(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "experiencia",
                 "entries": [], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia",
                 "entries": [], "table": []},
                {"title": "Proyectos", "kind": "proyectos",
                 "entries": [], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": []},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("kind mismatch" in w for w in warnings)

    def test_entry_count_mismatch(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": []},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("entry count" in w for w in warnings)

    def test_bullet_count_mismatch(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "Example University — Lima", "fecha": "",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["extra bullet not in base"]}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "", "fecha": "", "subtitulo": "",
                     "descriptor": "", "bullets": ["x"]},
                    {"titulo": "", "fecha": "", "subtitulo": "",
                     "descriptor": "", "bullets": ["x"]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [["Python", "x"], ["Excel", "SAP"]]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("bullet count" in w for w in warnings)

    def test_skills_table_shape_mismatch(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": ["only one"]}
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [["only one column"]]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("col count" in w or "Habilidades" in w for w in warnings)

    def test_immutable_field_drift_is_flagged(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "Example University — Lima", "fecha": "2021 – 2026",
                     "subtitulo": "Lic. en Economía | En proceso",
                     "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "ExampleCorp — Intern", "fecha": "WRONG DATE",
                     "subtitulo": "", "descriptor": "", "bullets": ["x"]},
                    {"titulo": "OtherCorp — Analyst", "fecha": "Dic 2023 – Abr 2024",
                     "subtitulo": "", "descriptor": "", "bullets": ["x"]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [["x", "x"], ["x", "x"]]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("immutable field" in w and "fecha" in w for w in warnings)

    def test_llm_including_enlaces_is_flagged(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": ["x"]},
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": ["x"]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "",
                     # LLM shouldn't have emitted this; should be flagged.
                     "enlaces": [{"texto": "x", "url": "evil://tampered"}],
                     "bullets": []},
                    {"titulo": "x", "fecha": "x", "subtitulo": "",
                     "descriptor": "", "bullets": []},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [["x", "x"], ["x", "x"]]},
            ],
        }
        warnings = _validate_shape(tailored, base)
        assert any("'enlaces'" in w and "must be omitted" in w for w in warnings)


# ---------- _reinject_enlaces tests ----------


class TestReinjectEnlaces:
    def test_reinjects_protected_urls_from_base(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "Example University — Lima", "fecha": "x",
                     "subtitulo": "", "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "ExampleCorp — Intern", "fecha": "x",
                     "subtitulo": "", "descriptor": "", "bullets": ["x"]},
                    {"titulo": "OtherCorp — Analyst", "fecha": "x",
                     "subtitulo": "", "descriptor": "", "bullets": ["x"]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "Rastreador de Gastos Automatizado con IA",
                     "fecha": "x", "subtitulo": "",
                     "descriptor": "(Dashboard)",
                     # LLM emission of enlaces would be overwritten:
                     "enlaces": [{"texto": "fake", "url": "evil"}],
                     "bullets": ["x", "y"]},
                    {"titulo": "KAYLA", "fecha": "x", "subtitulo": "",
                     "descriptor": "(Landing Page · Dashboard)",
                     "bullets": ["x", "y"]},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [["x", "y"], ["z", "w"]]},
            ],
        }
        _reinject_enlaces(tailored, base)
        # Proyectos entry 0 got the base URL, evil/LLM-tampered URL is gone:
        pr = tailored["sections"][2]["entries"][0]
        assert pr["enlaces"] == [{"texto": "Dashboard",
                                    "url": "https://example-dashboard.example.com/"}]
        # Proyectos entry 1 got its two base URLs in order:
        pr2 = tailored["sections"][2]["entries"][1]
        assert pr2["enlaces"] == [
            {"texto": "Landing Page", "url": "https://example-landing.example.com/"},
            {"texto": "Dashboard", "url": "https://example-dashboard-2.example.com/"},
        ]
        # Educación / Experiencia entries don't get enlaces (no base enlaces):
        assert "enlaces" not in tailored["sections"][0]["entries"][0]
        assert "enlaces" not in tailored["sections"][1]["entries"][0]

    def test_reinject_works_with_reordered_projects(self):
        """Reinjection survives project reordering — it matches by titulo."""
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia",
                 "entries": [], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    # KAYLA is now FIRST (reordered), Rastreador is second.
                    {"titulo": "KAYLA", "fecha": "x", "subtitulo": "",
                     "descriptor": "(Landing Page · Dashboard)",
                     "bullets": ["x", "y"]},
                    {"titulo": "Rastreador de Gastos Automatizado con IA",
                     "fecha": "x", "subtitulo": "",
                     "descriptor": "(Dashboard)",
                     "bullets": ["x", "y"]},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [["x", "y"], ["z", "w"]]},
            ],
        }
        _reinject_enlaces(tailored, base)
        pr0 = tailored["sections"][2]["entries"][0]
        assert pr0["enlaces"] == [
            {"texto": "Landing Page", "url": "https://example-landing.example.com/"},
            {"texto": "Dashboard", "url": "https://example-dashboard-2.example.com/"},
        ]
        pr1 = tailored["sections"][2]["entries"][1]
        assert pr1["enlaces"] == [{"texto": "Dashboard",
                                     "url": "https://example-dashboard.example.com/"}]

    def test_reinject_skips_habilidades_sections(self):
        base = _base_cv()
        tailored = {
            "summary": "x",
            "sections": [
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": []},
            ],
        }
        _reinject_enlaces(tailored, base)
        assert "enlaces" not in tailored["sections"][0]


# ---------- Summary-format validation (preserved from old tests) ----------


class TestSummaryFormatValidation:
    def test_keeps_template_no_warning(self):
        base = CVProfile(
            name="x", contact="y", contact_enlaces=[],
            summary="En búsqueda de un puesto en Digital Products · Análisis · Transf",
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
            name="x", contact="y", contact_enlaces=[],
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
            name="x", contact="y", contact_enlaces=[],
            summary="En búsqueda de un puesto en X · Y · Z",
            sections=[], raw_text="",
        )
        tailored = {
            "summary": "En búsqueda de un puesto en Data Engineering",
            "sections": [],
        }
        warnings = _validate_shape(tailored, base)
        assert any("separators" in w for w in warnings)

    def test_no_base_summary_no_warning(self):
        base = CVProfile(
            name="x", contact="y", contact_enlaces=[],
            summary="Data scientist with 5 years of experience",
            sections=[], raw_text="",
        )
        tailored = {"summary": "anything goes here", "sections": []}
        warnings = _validate_shape(tailored, base)
        assert not any("summary" in w for w in warnings)


# ---------- tailor_cv tests ----------


class TestTailorCV:
    def _valid_tailored(self) -> dict:
        return {
            "summary": "En búsqueda de un puesto en Ingeniería de Datos · Pipelines · Cloud",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "Example University — Lima", "fecha": "2021 – 2026",
                     "subtitulo": "Lic. en Economía | En proceso",
                     "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "ExampleCorp — Intern", "fecha": "Nov 2024 – Feb 2025",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["Automaticé la validación de facturas en Excel y SAP."]},
                    {"titulo": "OtherCorp — Analyst", "fecha": "Dic 2023 – Abr 2024",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["Optimicé procesos internos de validación de pagos."]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "Rastreador de Gastos Automatizado con IA", "fecha": "May 2026",
                     "subtitulo": "", "descriptor": "(Dashboard)",
                     "bullets": ["Automaté una herramienta de análisis bancario.",
                                  "Construí un dashboard Streamlit para análisis."]},
                    {"titulo": "KAYLA", "fecha": "Jun 2026",
                     "subtitulo": "", "descriptor": "(Landing Page · Dashboard)",
                     "bullets": ["Diseñé recordatorios médicos automatizados.",
                                  "Construí un dashboard de monitoreo."]},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [
                     ["Python", "Pandas, NumPy, Streamlit, Selenium"],
                     ["Excel", "SAP"],
                 ]},
            ],
        }

    def test_routes_correct_payload_through_stub(self):
        base = _base_cv()
        canned = self._valid_tailored()
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
        canned = self._valid_tailored()
        stub = StubLLMClient([llm_response(_json(canned))])
        tailor_cv(stub, base, _job(), model="glm-5.2")
        user = stub.calls[0]["user"]
        # URLs of project links must NEVER reach the LLM:
        assert "https://example-dashboard.example.com/" not in user
        assert "https://example-landing.example.com/" not in user
        # Descriptors (visible text without URLs) ARE sent:
        assert "(Dashboard)" in user
        assert "(Landing Page · Dashboard)" in user

    def test_enlaces_reinjected_after_response(self):
        base = _base_cv()
        canned = self._valid_tailored()
        # Note: canned output has NO `enlaces` field; the orchestrator injects.
        stub = StubLLMClient([llm_response(_json(canned))])
        result = tailor_cv(stub, base, _job(), model="glm-5.2")
        pr0 = result.tailored_json["sections"][2]["entries"][0]
        pr1 = result.tailored_json["sections"][2]["entries"][1]
        assert pr0["enlaces"] == [
            {"texto": "Dashboard", "url": "https://example-dashboard.example.com/"}
        ]
        assert pr1["enlaces"] == [
            {"texto": "Landing Page", "url": "https://example-landing.example.com/"},
            {"texto": "Dashboard", "url": "https://example-dashboard-2.example.com/"},
        ]

    def test_handles_markdown_fenced_json(self):
        base = _base_cv()
        valid = _json(self._valid_tailored())
        fenced = f"```json\n{valid}\n```"
        stub = StubLLMClient([llm_response(fenced)])
        result = tailor_cv(stub, base, _job())
        assert result.tailored_json["summary"].startswith("En búsqueda")

    def test_records_shape_warnings_on_bad_output(self):
        base = _base_cv()
        bad = _json({
            "summary": "x",
            "sections": [
                {"title": "WRONG", "kind": "x", "entries": [], "table": []},
            ],
        })
        stub = StubLLMClient([llm_response(bad)])
        result = tailor_cv(stub, base, _job())
        assert result.shape_warnings


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

    def test_accepts_verdict_key_alias(self):
        """Some models emit `verdict` instead of `overall_verdict`. Both must
        be honored so a true `pass` isn't silently downgraded to needs_repair.
        """
        canned = {"issues": [], "verdict": "pass", "summary": "ok"}
        stub = StubLLMClient([llm_response(_json(canned))])
        result = evaluate(stub, _base_cv(), _job(), {"summary": "x", "sections": []})
        assert result.verdict == "pass"
        assert not result.needs_repair


# ---------- repair tests ----------


class TestRepair:
    def test_routes_issues_to_prompt(self):
        valid = _json({
            "summary": "x fixed",
            "sections": [
                {"title": "Educación", "kind": "educacion", "entries": [
                    {"titulo": "Example University — Lima", "fecha": "2021 – 2026",
                     "subtitulo": "Lic. en Economía | En proceso",
                     "descriptor": "", "bullets": []}
                ], "table": []},
                {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                    {"titulo": "ExampleCorp — Intern", "fecha": "Nov 2024 – Feb 2025",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["Automaticé la validación de facturas en Excel."]},
                    {"titulo": "OtherCorp — Analyst", "fecha": "Dic 2023 – Abr 2024",
                     "subtitulo": "", "descriptor": "",
                     "bullets": ["Optimicé procesos internos de pagos."]},
                ], "table": []},
                {"title": "Proyectos", "kind": "proyectos", "entries": [
                    {"titulo": "Rastreador de Gastos Automatizado con IA", "fecha": "May 2026",
                     "subtitulo": "", "descriptor": "(Dashboard)",
                     "bullets": ["b1", "b2"]},
                    {"titulo": "KAYLA", "fecha": "Jun 2026",
                     "subtitulo": "", "descriptor": "(Landing Page · Dashboard)",
                     "bullets": ["b1", "b2"]},
                ], "table": []},
                {"title": "Habilidades & Herramientas", "kind": "habilidades",
                 "entries": [], "table": [
                     ["Python", "Pandas, NumPy, Jupyter"], ["Excel", "SAP"]
                 ]},
            ],
        })
        stub = StubLLMClient([llm_response(valid)])
        issues = [{"id": "1", "type": "verbatim_copy", "severity": "high",
                   "quote": "pipelines ETL en cloud"}]
        result = repair_cv(stub, _base_cv(), {"summary": "x", "sections": []}, issues)
        assert isinstance(result, RepairResult)
        assert result.repaired_json["summary"] == "x fixed"
        assert stub.calls[0]["model"]

    def test_validation_warnings_propagate(self):
        bad = _json({
            "summary": "x",
            "sections": [
                {"title": "WRONG", "kind": "", "entries": [], "table": []},
            ],
        })
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
        assert base.name in user_p
        assert "Snowflake" in user_p  # job desc text

    def test_tailor_system_says_urls_protected(self):
        base = _base_cv()
        sys_p, _ = build_tailor_prompt(base, _job())
        assert "url" in sys_p.lower()
        assert "protected" in sys_p.lower() or "inventory" in sys_p.lower() \
               or "out of scope" in sys_p.lower() or "intentionally" in sys_p.lower()

    def test_evaluator_includes_all_three(self):
        base = _base_cv()
        sys_p, user_p = build_evaluator_prompt(base, _job(),
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