"""Tests for run.py — CLI plumbing and incremental cache logic.

We don't invoke the LLM or any MCP server here; we patch the subsystems.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import run as run_module
from run import (
    _is_processed,
    _job_cache_path,
    _load_cached_jobs,
    _save_job_cache,
    _tailor_one,
)
from src.config import settings
from src.extract.linkedin_scraper import SavedJob
from src.profile.cv_reader import CVEntry, CVProfile, CVSection
from tests.test_helpers_llm import StubLLMClient, llm_response


def _make_job(title="Senior Data Engineer",
              url="https://www.linkedin.com/jobs/view/12345/",
              company="Acme", job_id="12345") -> SavedJob:
    return SavedJob(
        title=title, url=url, company=company, location="Lima, Peru",
        description="Buscamos Data Engineer con SQL, Python, Snowflake.",
        job_id=job_id,
    )


@pytest.fixture
def jobs_dir(tmp_path):
    """Redirect settings.jobs_dir to a tmp_path for the duration of one test."""
    fake = tmp_path / "jobs"
    fake.mkdir(parents=True, exist_ok=True)
    original = run_module.settings.jobs_dir
    object.__setattr__(run_module.settings, "jobs_dir", fake)
    try:
        yield fake
    finally:
        object.__setattr__(run_module.settings, "jobs_dir", original)


class TestCacheHelpers:
    def test_job_cache_path_uses_job_id(self):
        job = _make_job()
        p = _job_cache_path(job)
        assert p.name == "12345.json"
        assert p.parent == settings.jobs_dir

    def test_job_cache_path_falls_back_to_url(self):
        job = _make_job(job_id="", url="https://www.linkedin.com/jobs/view/abc-def/")
        p = _job_cache_path(job)
        assert p.name.endswith(".json")
        assert "abc" in p.name or "def" in p.name

    def test_unprocessed_when_no_cache(self, jobs_dir):
        assert _is_processed(_make_job()) is False

    def test_save_and_load_round_trip(self, jobs_dir):
        job = _make_job()
        _save_job_cache(job, tailored=False)
        loaded = _load_cached_jobs()
        assert len(loaded) == 1
        assert loaded[0].title == job.title
        assert loaded[0].job_id == job.job_id

    def test_is_processed_reads_tailored_flag(self, jobs_dir):
        _save_job_cache(_make_job(), tailored=True)
        assert _is_processed(_make_job()) is True

    def test_unmarked_job_is_not_processed(self, jobs_dir):
        _save_job_cache(_make_job(), tailored=False)
        assert _is_processed(_make_job()) is False


def _base_profile() -> CVProfile:
    """Base profile mirroring the new schema (typed entries)."""
    return CVProfile(
        name="ALEX",
        contact="email",
        contact_enlaces=[],
        summary="x",
        sections=[
            CVSection(title="Educación", kind="educacion", entries=[
                CVEntry(titulo="Example University — Lima", fecha="2021 – 2026",
                        subtitulo="Lic. en Economía | En proceso")
            ]),
            CVSection(title="Experiencia Laboral", kind="experiencia", entries=[
                CVEntry(titulo="ExampleCorp — Intern", fecha="Nov 2024 – Feb 2025",
                        bullets=["Automaticé la validación de facturas."])
            ]),
            CVSection(title="Proyectos", kind="proyectos", entries=[
                CVEntry(titulo="Project X", fecha="2026",
                        descriptor="(Tool)",
                        bullets=["Bullet 1.", "Bullet 2."])
            ]),
            CVSection(title="Habilidades & Herramientas", kind="habilidades",
                      table=[["Python", "Pandas"], ["Excel", "SAP"]]),
        ],
        raw_text="...",
    )


def _valid_tailored_json() -> str:
    return json.dumps({
        "summary": "x",
        "sections": [
            {"title": "Educación", "kind": "educacion", "entries": [
                {"titulo": "Example University — Lima", "fecha": "2021 – 2026",
                 "subtitulo": "Lic. en Economía | En proceso",
                 "descriptor": "", "bullets": []}
            ], "table": []},
            {"title": "Experiencia Laboral", "kind": "experiencia", "entries": [
                {"titulo": "ExampleCorp — Intern", "fecha": "Nov 2024 – Feb 2025",
                 "subtitulo": "", "descriptor": "",
                 "bullets": ["Automaticé la validación de facturas."]}
            ], "table": []},
            {"title": "Proyectos", "kind": "proyectos", "entries": [
                {"titulo": "Project X", "fecha": "2026",
                 "subtitulo": "", "descriptor": "(Tool)",
                 "bullets": ["Bullet 1.", "Bullet 2."]}
            ], "table": []},
            {"title": "Habilidades & Herramientas", "kind": "habilidades",
             "entries": [], "table": [["Python", "Pandas"], ["Excel", "SAP"]]},
        ],
    }, ensure_ascii=False)


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Redirect settings.output_dir + base_cv_path + html/pdf renderers."""
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    base = _fake_base_html(tmp_path)
    orig_out = run_module.settings.output_dir
    orig_base = run_module.settings.base_cv_path
    object.__setattr__(run_module.settings, "output_dir", out)
    object.__setattr__(run_module.settings, "base_cv_path", base)
    # Patch renderers to no-op so tests don't require Playwright/Jinja.
    monkeypatch.setattr(
        run_module, "_render_job_html_pdf",
        lambda tailored, out_dir: (out_dir / "cv.html", out_dir / "cv.pdf"),
    )
    try:
        yield out
    finally:
        object.__setattr__(run_module.settings, "output_dir", orig_out)
        object.__setattr__(run_module.settings, "base_cv_path", orig_base)


class TestTailorOne:
    def test_dry_run_creates_job_description_only(self, output_dir):
        job = _make_job()
        out = _tailor_one(client=None, base_profile=_base_profile(), job=job, dry_run=True)
        assert out is not None
        assert (out / "job_description.txt").exists()
        # No analysis.json in dry-run
        assert not (out / "analysis.json").exists()

    def test_full_run_produces_analysis_and_evaluation(self, output_dir):
        """Full run with stubbed LLM + patched HTML/PDF renderer."""
        stub = StubLLMClient([
            llm_response(_valid_tailored_json()),
            llm_response(json.dumps({"issues": [], "overall_verdict": "pass",
                                      "summary": "ok"})),
        ])
        job = _make_job()
        out = _tailor_one(client=stub, base_profile=_base_profile(),
                         job=job, dry_run=False)
        assert out is not None
        assert (out / "analysis.json").exists()
        assert (out / "evaluation.json").exists()
        # No repaired file when verdict is pass
        assert not (out / "analysis_repaired.json").exists()

    def test_repair_pass_writes_repaired_json(self, output_dir):
        stub = StubLLMClient([
            llm_response(_valid_tailored_json()),
            llm_response(json.dumps({
                "issues": [{
                    "id": "1", "type": "verbatim_copy", "severity": "high",
                    "quote": "pipelines ETL", "explanation": "copied",
                    "suggested_fix": "paraphrase"
                }],
                "overall_verdict": "needs_repair", "summary": "x"
            })),
            llm_response(_valid_tailored_json()),  # repair returns corrected
        ])
        out = _tailor_one(client=stub, base_profile=_base_profile(),
                         job=_make_job(), dry_run=False)
        assert (out / "analysis_repaired.json").exists()

    def test_tailored_with_no_sections_is_failure(self, output_dir):
        bad = json.dumps({"summary": "x", "sections": []})
        stub = StubLLMClient([llm_response(bad)])
        result = _tailor_one(client=stub, base_profile=_base_profile(),
                              job=_make_job(), dry_run=False)
        assert result is None


def _fake_base_html(tmp_path: Path) -> Path:
    """Build a minimal HTML fixture mirroring the real CV structure."""
    html = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>x</title></head>
<body>
  <div class="header">
    <div class="header-main">
      <p class="name">ALEX</p>
      <p class="contact-line">email</p>
      <p class="tagline">En búsqueda de un puesto en x</p>
    </div>
  </div>

  <div class="section">
    <p class="section-title">Educación</p>
    <div class="entry-row">
      <span class="entry-title">Example University — Lima</span>
      <span class="entry-date">2021 – 2026</span>
    </div>
    <p class="entry-subtitle">Lic. en Economía | En proceso</p>
  </div>

  <div class="section">
    <p class="section-title">Experiencia Laboral</p>
    <div class="entry-block">
      <div class="entry-row">
        <span class="entry-title">ExampleCorp — Intern</span>
        <span class="entry-date">Nov 2024 – Feb 2025</span>
      </div>
      <ul class="bullets"><li>Automaticé la validación de facturas.</li></ul>
    </div>
  </div>

  <div class="section">
    <p class="section-title">Proyectos</p>
    <div class="project-block">
      <div class="project-header">
        <span class="project-title">Project X</span>
        <span class="entry-date">2026</span>
      </div>
      <p class="project-links">(Tool)</p>
      <ul class="bullets"><li>Bullet 1.</li><li>Bullet 2.</li></ul>
    </div>
  </div>

  <div class="section">
    <p class="section-title">Habilidades &amp; Herramientas</p>
    <table class="skills-table">
      <tr><td class="skill-label">Python</td><td>Pandas</td></tr>
      <tr><td class="skill-label">Excel</td><td>SAP</td></tr>
    </table>
  </div>
</body></html>
"""
    path = tmp_path / "base_cv.html"
    path.write_text(html, encoding="utf-8")
    return path


class TestArgparseAndCommands:
    def test_parser_requires_subcommand(self):
        with pytest.raises(SystemExit):
            run_module.build_parser().parse_args([])

    def test_extract_subcommand(self):
        p = run_module.build_parser()
        args = p.parse_args(["extract"])
        assert args.cmd == "extract"

    def test_all_with_flags(self):
        p = run_module.build_parser()
        args = p.parse_args(["all", "--new", "--force", "--dry-run",
                             "--job", "https://example.com", "--limit", "3",
                             "--scraper", "browsermcp", "--legacy-docx"])
        assert args.cmd == "all"
        assert args.new is True
        assert args.force is True
        assert args.dry_run is True
        assert args.job == "https://example.com"
        assert args.limit == 3
        assert args.scraper == "browsermcp"
        assert args.legacy_docx is True

    def test_tailor_only_command(self):
        p = run_module.build_parser()
        args = p.parse_args(["tailor"])
        assert args.cmd == "tailor"
        assert args.new is False

    def test_review_command_exists(self):
        p = run_module.build_parser()
        args = p.parse_args(["review", "2026-07-13_senior-data-engineer_acme"])
        assert args.cmd == "review"
        assert args.job_slug == "2026-07-13_senior-data-engineer_acme"

    def test_review_command_optional_flags(self):
        p = run_module.build_parser()
        args = p.parse_args(["review", "slug", "--port", "9000", "--host",
                             "0.0.0.0", "--no-browser"])
        assert args.port == 9000
        assert args.host == "0.0.0.0"
        assert args.no_browser is True

    def test_main_smoke_for_tailor_no_jobs(self, tmp_path):
        from run import main
        original = run_module.settings.jobs_dir
        object.__setattr__(run_module.settings, "jobs_dir", tmp_path / "nojobs")
        try:
            rc = main(["tailor"])
            assert rc == 0
        finally:
            object.__setattr__(run_module.settings, "jobs_dir", original)


class TestListCommand:
    def test_list_subcommand_registered(self):
        p = run_module.build_parser()
        args = p.parse_args(["list"])
        assert args.cmd == "list"

    def test_resolve_nested_date_slug(self, tmp_path):
        out = tmp_path / "output"
        (out / "2026-07-23" / "practicante-pro-comercial_apparka").mkdir(parents=True)
        orig = run_module.settings.output_dir
        object.__setattr__(run_module.settings, "output_dir", out)
        try:
            d = run_module._resolve_job_output_dir(
                "2026-07-23/practicante-pro-comercial_apparka"
            )
            assert d == out / "2026-07-23" / "practicante-pro-comercial_apparka"
        finally:
            object.__setattr__(run_module.settings, "output_dir", orig)

    def test_resolve_bare_slug_searches_dates(self, tmp_path):
        out = tmp_path / "output"
        (out / "2026-07-23" / "practicante-pro-comercial_apparka").mkdir(parents=True)
        orig = run_module.settings.output_dir
        object.__setattr__(run_module.settings, "output_dir", out)
        try:
            d = run_module._resolve_job_output_dir("practicante-pro-comercial_apparka")
            assert d == out / "2026-07-23" / "practicante-pro-comercial_apparka"
        finally:
            object.__setattr__(run_module.settings, "output_dir", orig)

    def test_resolve_legacy_flat_form(self, tmp_path):
        out = tmp_path / "output"
        (out / "2026-07-22" / "practicante-pro-comercial_apparka").mkdir(parents=True)
        orig = run_module.settings.output_dir
        object.__setattr__(run_module.settings, "output_dir", out)
        try:
            # Legacy slug form  "<date>_<slug>"  should resolve to the nested dir.
            d = run_module._resolve_job_output_dir(
                "2026-07-22_practicante-pro-comercial_apparka"
            )
            assert d == out / "2026-07-22" / "practicante-pro-comercial_apparka"
        finally:
            object.__setattr__(run_module.settings, "output_dir", orig)

    def test_resolve_unknown_returns_none(self, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True)
        orig = run_module.settings.output_dir
        object.__setattr__(run_module.settings, "output_dir", out)
        try:
            assert run_module._resolve_job_output_dir("does-not-exist") is None
        finally:
            object.__setattr__(run_module.settings, "output_dir", orig)


class TestRepairFiltering:
    """The url_tampered-only case should NOT trigger a repair LLM call."""

    def test_only_url_tampered_skips_repair(self, output_dir):
        canned_tailor = _valid_tailored_json()
        canned_eval = json.dumps({
            "issues": [
                {"id": "1", "type": "url_tampered", "severity": "high",
                 "quote": "enlaces", "base_quote": None,
                 "explanation": "stray enlaces", "suggested_fix": "remove"},
            ],
            "overall_verdict": "needs_repair", "summary": "url tampered",
        })
        stub = StubLLMClient([
            llm_response(canned_tailor),
            llm_response(canned_eval),
            # If repair were invoked, it would need a 3rd response; its absence
            # would raise on StubLLMClient. Asserting no exception.
        ])
        out = _tailor_one(
            client=stub, base_profile=_base_profile(),
            job=_make_job(), dry_run=False,
        )
        assert out is not None
        # evaluation.json written, no analysis_repaired.json (no repair).
        assert (out / "evaluation.json").exists()
        assert not (out / "analysis_repaired.json").exists()
        # Only 2 LLM calls: tailor + evaluate (no repair).
        assert len(stub.calls) == 2