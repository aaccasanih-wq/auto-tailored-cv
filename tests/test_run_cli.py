"""Tests for run.py — CLI plumbing, incremental cache logic, and the pipeline
stages (now including the job_summarizer pass).

We don't invoke the LLM or any MCP server here; we patch the subsystems and use
a stub LLM client.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import run as run_module
from run import (
    _already_generated,
    _is_processed,
    _job_cache_path,
    _load_cached_jobs,
    _load_index,
    _save_job_cache,
    _tailor_one,
    _upsert_job_cache,
)
from src.config import settings
from src.extract.linkedin_scraper import SavedJob
from src.profile.cv_reader import CVBullet, CVEntry, CVItem, CVProfile, CVSection, PersonalInfo
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


class TestRegistry:
    """The `jobs/_index.json` registry keyed by job_id (dedup across URL
    variants + state preservation on re-extraction)."""

    def test_save_job_cache_writes_index(self, jobs_dir):
        job = _make_job()
        _save_job_cache(job, tailored=True, cv_generated_at="2026-08-02T10:00:00+00:00",
                        cv_pdf_path="output/x/cv.pdf")
        index = _load_index()
        rec = index.get("12345")
        assert rec is not None
        assert rec["tailored"] is True
        assert rec["cv_generated_at"] == "2026-08-02T10:00:00+00:00"
        assert rec["cv_pdf_path"] == "output/x/cv.pdf"
        assert rec["status"] == "done"

    def test_upsert_job_cache_preserves_tailored_state(self, jobs_dir):
        """A re-extraction (upsert) must NOT reset tailored=true of an offer
        that already has a CV — this is what fixes the old `all` reset bug."""
        _save_job_cache(_make_job(), tailored=True, cv_generated_at="2026-08-01T09:00:00+00:00",
                        cv_pdf_path="output/a/cv.pdf")
        # Re-scrape the same offer (same job_id), no generation info.
        _upsert_job_cache(_make_job(title="Same Offer Renamed"))
        assert _is_processed(_make_job()) is True
        data = json.loads(_job_cache_path(_make_job()).read_text(encoding="utf-8"))
        assert data["tailored"] is True
        assert data["cv_pdf_path"] == "output/a/cv.pdf"
        rec = _load_index().get("12345")
        assert rec["tailored"] is True
        assert rec["status"] == "done"

    def test_index_keyed_by_job_id_not_url(self, jobs_dir):
        """Same job reached via different URL variants maps to ONE registry key."""
        _upsert_job_cache(_make_job(url="https://www.linkedin.com/jobs/view/12345/"))
        _upsert_job_cache(_make_job(url="https://www.linkedin.com/jobs/search-results/?currentJobId=12345"))
        index = _load_index()
        ids = {k for k in index.keys()}
        assert ids == {"12345"}

    def test_already_generated_detects_pasted_link(self, jobs_dir):
        _save_job_cache(_make_job(), tailored=True, cv_generated_at="2026-08-01T09:00:00+00:00",
                        cv_pdf_path="output/a/cv.pdf")
        # User pastes a DIFFERENT URL variant of the same offer.
        rec = _already_generated(
            "https://www.linkedin.com/jobs/search-results/?currentJobId=12345&refId=abc"
        )
        assert rec is not None
        assert rec["cv_pdf_path"] == "output/a/cv.pdf"

    def test_already_generated_returns_none_for_unknown(self, jobs_dir):
        assert _already_generated("https://www.linkedin.com/jobs/view/999999/") is None


def _base_profile() -> CVProfile:
    """Base profile mirroring the new generic schema."""
    return CVProfile(
        personal_info=PersonalInfo(name="MARÍA", email="maria@example.com"),
        summary="Perfil orientada a datos.",
        sections=[
            CVSection(id="exp", title="Experiencia Laboral", type="entry_block",
                      reorderable=False, entries=[
                CVEntry(heading="ExampleCorp — Intern", dates="Nov 2024 – Feb 2025",
                        bullets=[CVBullet(text="Automaticé la validación de facturas.")]),
            ]),
            CVSection(id="proy", title="Proyectos", type="entry_block",
                      reorderable=True, entries=[
                CVEntry(heading="Project X", dates="2026", links=[],
                        bullets=[CVBullet(text="Bullet 1."), CVBullet(text="Bullet 2.")]),
            ]),
            CVSection(id="hab", title="Habilidades & Herramientas", type="simple_list",
                      items=[CVItem(text="Python"), CVItem(text="SQL")]),
        ],
        raw_text="...",
    )


def _valid_job_summary() -> str:
    return json.dumps({
        "requisitos_duros": ["SQL", "Python"],
        "skills_deseadas": ["Snowflake"],
        "funciones_clave": ["Construir pipelines"],
    }, ensure_ascii=False)


def _valid_tailored_json() -> str:
    return json.dumps({
        "summary": "Perfil orientada a datos y automatización.",
        "sections": [
            {"id": "exp", "title": "Experiencia Laboral", "type": "entry_block",
             "reorderable": False, "entries": [
                {"heading": "ExampleCorp — Intern", "subheading": "", "location": "",
                 "dates": "Nov 2024 – Feb 2025",
                 "bullets": [{"text": "Automaticé la validación de facturas.", "tags": []}]}
             ], "items": [], "text": ""},
            {"id": "proy", "title": "Proyectos", "type": "entry_block",
             "reorderable": True, "entries": [
                {"heading": "Project X", "subheading": "", "location": "",
                 "dates": "2026", "bullets": [
                    {"text": "Bullet 1.", "tags": []}, {"text": "Bullet 2.", "tags": []}]}
             ], "items": [], "text": ""},
            {"id": "hab", "title": "Habilidades & Herramientas", "type": "simple_list",
             "reorderable": False, "entries": [], "items": [
                {"text": "Python", "tags": []}, {"text": "SQL", "tags": []}], "text": ""},
        ],
    }, ensure_ascii=False)


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    """Redirect settings.output_dir + base_cv_path + html/pdf renderers."""
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)
    base = _fake_base_yaml(tmp_path)
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
    def test_dry_run_creates_no_filesystem_side_effects(self, output_dir):
        job = _make_job()
        out = _tailor_one(client=None, base_profile=_base_profile(), job=job, dry_run=True)
        assert out is not None
        # Dry-run is purely informational: it must NOT create the output
        # folder, write job_description.txt, or touch any file under output/.
        assert not out.exists()
        assert not (out / "job_description.txt").exists()
        assert not (out / "analysis.json").exists()

    def test_full_run_produces_analysis_and_evaluation(self, output_dir):
        """Full run with stubbed LLM + patched HTML/PDF renderer."""
        stub = StubLLMClient([
            llm_response(_valid_job_summary()),          # 1: summarize
            llm_response(_valid_tailored_json()),        # 2: tailor
            llm_response(json.dumps({"issues": [], "overall_verdict": "pass",
                                      "summary": "ok"})),  # 3: evaluate
        ])
        job = _make_job()
        out = _tailor_one(client=stub, base_profile=_base_profile(),
                         job=job, dry_run=False)
        assert out is not None
        assert (out / "analysis.json").exists()
        assert (out / "evaluation.json").exists()
        assert (out / "job_summary.json").exists()
        # No repaired file when verdict is pass
        assert not (out / "analysis_repaired.json").exists()

    def test_job_summary_is_cached_not_recomputed(self, output_dir):
        """A cached job_summary.json is reused — the summarize LLM call is
        skipped (only tailor + evaluate run)."""
        stub = StubLLMClient([
            llm_response(_valid_tailored_json()),
            llm_response(json.dumps({"issues": [], "overall_verdict": "pass",
                                      "summary": "ok"})),
        ])
        job = _make_job()
        out = _resolve_out(output_dir, job)
        out.mkdir(parents=True, exist_ok=True)
        (out / "job_summary.json").write_text(
            json.dumps({"requisitos_duros": ["cached"], "skills_deseadas": [],
                        "funciones_clave": []}), encoding="utf-8")
        _tailor_one(client=stub, base_profile=_base_profile(), job=job, dry_run=False)
        assert len(stub.calls) == 2  # no summarize call

    def test_force_recomputes_job_summary(self, output_dir):
        """With --force, even a cached summary is recomputed."""
        stub = StubLLMClient([
            llm_response(_valid_job_summary()),
            llm_response(_valid_tailored_json()),
            llm_response(json.dumps({"issues": [], "overall_verdict": "pass",
                                      "summary": "ok"})),
        ])
        job = _make_job()
        out = _resolve_out(output_dir, job)
        out.mkdir(parents=True, exist_ok=True)
        (out / "job_summary.json").write_text(
            json.dumps({"requisitos_duros": ["stale"], "skills_deseadas": [],
                        "funciones_clave": []}), encoding="utf-8")
        _tailor_one(client=stub, base_profile=_base_profile(), job=job,
                    dry_run=False, force=True)
        assert len(stub.calls) == 3  # summarize recomputed

    def test_repair_pass_writes_repaired_json(self, output_dir):
        stub = StubLLMClient([
            llm_response(_valid_job_summary()),
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
        stub = StubLLMClient([llm_response(_valid_job_summary()), llm_response(bad)])
        result = _tailor_one(client=stub, base_profile=_base_profile(),
                              job=_make_job(), dry_run=False)
        assert result is None

    def test_evaluation_disabled_skips_evaluate(self, output_dir):
        """ENABLE_EVALUATION=false: only summarize + tailor are called; no
        evaluate/repair, and a cv.pdf is still produced (renderer is patched
        here, so we assert no evaluation.json is written)."""
        orig = run_module.settings.enable_evaluation
        object.__setattr__(run_module.settings, "enable_evaluation", False)
        try:
            stub = StubLLMClient([
                llm_response(_valid_job_summary()),
                llm_response(_valid_tailored_json()),
            ])
            out = _tailor_one(client=stub, base_profile=_base_profile(),
                              job=_make_job(), dry_run=False)
            assert out is not None
            assert (out / "analysis.json").exists()
            assert not (out / "evaluation.json").exists()
            assert len(stub.calls) == 2
        finally:
            object.__setattr__(run_module.settings, "enable_evaluation", orig)


def _resolve_out(out_dir: Path, job: SavedJob) -> Path:
    return run_module._resolve_job_folder(job)


def _fake_base_yaml(tmp_path: Path) -> Path:
    """Build a minimal YAML fixture mirroring the real base CV structure."""
    yaml_text = """personal_info:
  name: "MARÍA"
  email: "maria@example.com"
summary: "Perfil orientada a datos."
sections:
  - id: exp
    title: "Experiencia Laboral"
    type: entry_block
    reorderable: false
    entries:
      - heading: "ExampleCorp — Intern"
        dates: "Nov 2024 – Feb 2025"
        bullets:
          - text: "Automaticé la validación de facturas."
  - id: proy
    title: "Proyectos"
    type: entry_block
    reorderable: true
    entries:
      - heading: "Project X"
        dates: "2026"
        bullets:
          - text: "Bullet 1."
          - text: "Bullet 2."
  - id: hab
    title: "Habilidades & Herramientas"
    type: simple_list
    items:
      - text: "Python"
      - text: "SQL"
"""
    path = tmp_path / "base_cv.yaml"
    path.write_text(yaml_text, encoding="utf-8")
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

    def test_all_positional_job_url(self):
        p = run_module.build_parser()
        args = p.parse_args(["all", "https://linkedin.com/jobs/view/123/"])
        assert args.cmd == "all"
        assert args.job_url == "https://linkedin.com/jobs/view/123/"
        assert args.job is None

    def test_tailor_positional_job_url(self):
        p = run_module.build_parser()
        args = p.parse_args(["tailor", "https://linkedin.com/jobs/view/456/"])
        assert args.cmd == "tailor"
        assert args.job_url == "https://linkedin.com/jobs/view/456/"

    def test_tailor_yes_flag(self):
        p = run_module.build_parser()
        args = p.parse_args(["tailor", "--force", "--yes"])
        assert args.yes is True
        assert args.force is True

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
        canned_eval = json.dumps({
            "issues": [
                {"id": "1", "type": "url_tampered", "severity": "high",
                 "quote": "links", "base_quote": None,
                 "explanation": "stray links", "suggested_fix": "remove"},
            ],
            "overall_verdict": "needs_repair", "summary": "url tampered",
        })
        stub = StubLLMClient([
            llm_response(_valid_job_summary()),
            llm_response(_valid_tailored_json()),
            llm_response(canned_eval),
            # If repair were invoked, it would need a 4th response; its absence
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
        # Only 3 LLM calls: summarize + tailor + evaluate (no repair).
        assert len(stub.calls) == 3


class TestBulkConfirmGuard:
    """The `_needs_bulk_confirm` helper decides whether the CLI blocks on a
    y/N prompt before letting `tailor`/`all` re-process more than one job."""

    def _ns(self, **kw):
        ns = argparse.Namespace(
            dry_run=False, force=False, yes=False, job=None, new=False,
            limit=0,
        )
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_single_job_no_prompt(self):
        assert run_module._needs_bulk_confirm(self._ns(force=True), 1) is False

    def test_dry_run_no_prompt_even_if_force(self):
        assert run_module._needs_bulk_confirm(
            self._ns(force=True, dry_run=True), 10) is False

    def test_no_force_no_prompt(self):
        assert run_module._needs_bulk_confirm(self._ns(force=False), 10) is False

    def test_bulk_force_triggers_prompt(self):
        assert run_module._needs_bulk_confirm(self._ns(force=True), 5) is True

    def test_yes_flag_skips_prompt(self):
        assert run_module._needs_bulk_confirm(self._ns(force=True, yes=True), 5) is False

    def test_zero_jobs_no_prompt(self):
        assert run_module._needs_bulk_confirm(self._ns(force=True), 0) is False

    def test_confirm_bulk_yes(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _p: "y")
        assert run_module._confirm_bulk(3) is True

    def test_confirm_bulk_no(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _p: "n")
        assert run_module._confirm_bulk(3) is False

    def test_confirm_bulk_eof_aborts(self, monkeypatch):
        def _raise(_p):
            raise EOFError
        monkeypatch.setattr("builtins.input", _raise)
        assert run_module._confirm_bulk(3) is False

    def test_tailor_bulk_force_aborts_on_no(self, jobs_dir, output_dir, monkeypatch):
        """End-to-end: 3 jobs cached, `tailor --force` (no --job), stdin
        answers 'n' → rc=1, no LLM call made."""
        for i in range(3):
            _save_job_cache(_make_job(
                title=f"Job {i}", url=f"https://linkedin.com/jobs/view/100{i}/",
                job_id=str(100 + i),
            ), tailored=True)
        monkeypatch.setattr("builtins.input", lambda _p: "n")
        called = {"v": 0}
        def _fake_make():
            called["v"] += 1
            return None
        monkeypatch.setattr(run_module, "make_client", _fake_make)
        rc = run_module.main(["tailor", "--force"])
        assert rc == 1
        assert called["v"] == 0
