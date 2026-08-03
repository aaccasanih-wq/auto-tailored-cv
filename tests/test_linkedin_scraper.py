"""Tests for src/extract/linkedin_scraper.py — parser logic only.

The MCP transport itself requires launching the Browser MCP subprocess + a
real Chrome session, so we test only the pure-Python parsing functions here
(end-to-end is validated by running the CLI with BrowserMCP installed).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.extract.linkedin_scraper import (
    JOB_URL_RE,
    SavedJob,
    _extract_job_urls,
    _extract_saved_times,
    _job_id_from_url,
    _normalize_job_url,
    _parse_job_detail,
    _parse_relative_saved_to_iso,
)

FIXED_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _iso(label: str) -> str:
    return _parse_relative_saved_to_iso(label, now=FIXED_NOW)


class TestJobUrlRegex:
    def test_canonical_path(self):
        m = JOB_URL_RE.search("https://www.linkedin.com/jobs/view/senior-data-engineer-at-acme-1234567890/")
        assert m is not None
        assert m.group(1) == "1234567890"

    def test_numeric_only(self):
        m = JOB_URL_RE.search("https://www.linkedin.com/jobs/view/1234567890/")
        assert m is not None
        assert m.group(1) == "1234567890"

    def test_currentJobId_query(self):
        m = JOB_URL_RE.search(
            "https://www.linkedin.com/jobs/view/?currentJobId=9876543210&refId=abc"
        )
        assert m is not None
        assert m.group(2) == "9876543210"

    def test_subdomain(self):
        m = JOB_URL_RE.search("https://jobs.linkedin.com/jobs/view/555")
        assert m is not None
        assert m.group(1) == "555"

    def test_non_match(self):
        m = JOB_URL_RE.search("https://www.linkedin.com/in/someone/")
        assert m is None

    def test_empty_input(self):
        assert JOB_URL_RE.search("") is None


class TestNormalizeJobUrl:
    def test_canonical_path_unchanged(self):
        url = "https://www.linkedin.com/jobs/view/1234567890/"
        assert _normalize_job_url(url) == url

    def test_view_with_current_job_id_unchanged(self):
        url = "https://www.linkedin.com/jobs/view/?currentJobId=9876543210&refId=abc"
        assert _normalize_job_url(url) == url

    def test_search_results_url_normalized(self):
        url = (
            "https://www.linkedin.com/jobs/search-results/?currentJobId=4429119711"
            "&eBP=CwEAAAA&refId=abc&trackingId=def"
        )
        assert _normalize_job_url(url) == "https://www.linkedin.com/jobs/view/4429119711/"

    def test_rewards_url_normalized(self):
        url = "https://www.linkedin.com/jobs/c/rewards/?currentJobId=555"
        assert _normalize_job_url(url) == "https://www.linkedin.com/jobs/view/555/"

    def test_no_current_job_id_unchanged(self):
        url = "https://www.linkedin.com/jobs/search/?keywords=data"
        assert _normalize_job_url(url) == url

    def test_job_posting_id_url_normalized(self):
        """Share links using `jobPostingId=` resolve to the canonical form."""
        url = "https://www.linkedin.com/jobs/view/?jobPostingId=9876543210&refId=xyz"
        assert _normalize_job_url(url) == "https://www.linkedin.com/jobs/view/9876543210/"

    def test_empty_string(self):
        assert _normalize_job_url("") == ""

    def test_non_linkedin_url_unchanged(self):
        url = "https://example.com/some/page"
        assert _normalize_job_url(url) == url


class TestJobIdFromUrl:
    def test_canonical(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/view/123/") == "123"

    def test_slugged_canonical(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/view/data-engineer-acme-456/") == "456"

    def test_view_with_current_job_id(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/view/?currentJobId=789") == "789"

    def test_search_current_job_id(self):
        assert _job_id_from_url(
            "https://www.linkedin.com/jobs/search-results/?currentJobId=4429119711&refId=a"
        ) == "4429119711"

    def test_rewards_current_job_id(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/c/rewards/?currentJobId=555") == "555"

    def test_share_job_posting_id(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/view/?jobPostingId=321") == "321"

    def test_no_id_returns_empty(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/search/?keywords=data") == ""

    def test_empty_returns_empty(self):
        assert _job_id_from_url("") == ""

    def test_non_linkedin_returns_empty(self):
        assert _job_id_from_url("https://example.com/x") == ""


class TestParseRelativeSavedToIso:
    def test_saved_today_es(self):
        assert _iso("Guardado hoy") == FIXED_NOW.isoformat(timespec="seconds")

    def test_saved_today_en(self):
        assert _iso("Saved today") == _iso("Guardado hoy")

    def test_saved_yesterday(self):
        assert _iso("Guardado ayer") == _iso("Saved yesterday")
        assert _iso("Guardado ayer") == "2026-07-31T12:00:00+00:00"

    def test_saved_days_ago_es(self):
        assert _iso("Guardado hace 3 días") == _iso("Saved 3 days ago")
        assert _iso("Guardado hace 3 días") == "2026-07-29T12:00:00+00:00"

    def test_saved_hours_ago_en(self):
        assert _iso("Saved 5 hours ago") == _iso("Guardado hace 5 horas")

    def test_saved_weeks_and_months(self):
        assert _iso("Guardado hace 2 semanas") == _iso("Saved 2 weeks ago")
        assert _iso("Guardado hace 1 mes") == _iso("Saved 1 month ago")

    def test_garbage_returns_empty(self):
        assert _iso("") == ""
        assert _iso("cualquier cosa") == ""

    def test_posting_date_not_confused_with_save_date(self):
        """'hace 1 día' alone is NOT a save label — it must start with
        'Guardado'/'Saved' to be treated as a save timestamp."""
        assert _iso("Lima, Perú · hace 1 día") == ""


class TestExtractSavedTimes:
    SNAPSHOT = """\
- link "Practicante Profesional de Planeamiento Comercial" [ref=a1]: https://www.linkedin.com/jobs/view/4447532297/
- paragraph [ref=a2]: Guardado hace 2 días
- link "Practicante Profesional de Mejora Continua" [ref=b1]: https://www.linkedin.com/jobs/view/4431977634/
- paragraph [ref=b2]: Guardado ayer
"""

    def test_pairs_labels_to_nearest_url(self):
        times = _extract_saved_times(self.SNAPSHOT, now=FIXED_NOW)
        assert times["https://www.linkedin.com/jobs/view/4447532297/"] == _iso("Guardado hace 2 días")
        assert times["https://www.linkedin.com/jobs/view/4431977634/"] == _iso("Guardado ayer")

    def test_label_before_url_is_paired(self):
        snap = """\
- paragraph: Guardado hace 1 semana
- link "X" [ref=a]: https://www.linkedin.com/jobs/view/111/
"""
        times = _extract_saved_times(snap, now=FIXED_NOW)
        assert times["https://www.linkedin.com/jobs/view/111/"] == _iso("Guardado hace 1 semana")

    def test_no_labels_returns_empty(self):
        snap = """\
- link "X" [ref=a]: https://www.linkedin.com/jobs/view/111/
"""
        assert _extract_saved_times(snap) == {}

    def test_canonicalizes_url_variants(self):
        snap = """\
- link "X" [ref=a]: https://www.linkedin.com/jobs/view/data-eng-555/
- paragraph: Saved 1 day ago
"""
        times = _extract_saved_times(snap, now=FIXED_NOW)
        assert times == {"https://www.linkedin.com/jobs/view/555/": _iso("Saved 1 day ago")}


class TestExtractJobUrls:
    def test_basic_list(self):
        snapshot = """
- main [ref=e1]:
  - link "Senior Data Engineer" [ref=e2]: https://www.linkedin.com/jobs/view/data-engineer-at-acme-100
  - link "Backend" [ref=e3]: https://www.linkedin.com/jobs/view/200
"""
        urls = _extract_job_urls(snapshot)
        assert len(urls) == 2
        assert urls[0] == "https://www.linkedin.com/jobs/view/100/"
        assert urls[1] == "https://www.linkedin.com/jobs/view/200/"

    def test_dedupes(self):
        snapshot = """
some text https://www.linkedin.com/jobs/view/9/
more text https://www.linkedin.com/jobs/view/9/
https://www.linkedin.com/jobs/view/?currentJobId=9&from=saved
"""
        urls = _extract_job_urls(snapshot)
        assert len(urls) == 1
        assert urls[0] == "https://www.linkedin.com/jobs/view/9/"

    def test_no_links_returns_empty(self):
        assert _extract_job_urls("no jobs here") == []


class TestParseJobDetail:
    @pytest.fixture
    def sample_snapshot(self) -> str:
        # Mirrors the real BrowserMCP v0.1.x output: Page Title header + flat
        # 'text:' rows + a few /url: lines for navigation chrome.
        return (
            "Page URL: https://www.linkedin.com/jobs/view/12345/\n"
            "Page Title: Senior Data Engineer | Acme Corporation | LinkedIn\n"
            "Page Snapshot\n"
            "```yaml\n"
            "text: Inicio\n"
            "/url: https://www.linkedin.com/mynetwork\n"
            "text: Mi red\n"
            "/url: https://www.linkedin.com/jobs/\n"
            "text: Empleos\n"
            "/url: https://www.linkedin.com/company/acme/\n"
            "text: Lima, Peru\n"
            "text: · hace 2 días\n"
            "text: Buscamos un Data Engineer con experiencia en pipelines ETL.\n"
            "text: Requisitos: SQL avanzado, Python, Snowflake.\n"
            "text: Beneficios: seguro médico, lunch tickets.\n"
            "text: Ver empleos similares\n"
            "/url: https://www.linkedin.com/jobs/view/12345/\n"
            "```\n"
        )

    def test_extracts_title(self, sample_snapshot):
        job = _parse_job_detail(sample_snapshot, "https://www.linkedin.com/jobs/view/12345/")
        assert job.title == "Senior Data Engineer"

    def test_extracts_company(self, sample_snapshot):
        job = _parse_job_detail(sample_snapshot, "https://www.linkedin.com/jobs/view/12345/")
        assert job.company == "Acme Corporation"

    def test_extracts_location(self, sample_snapshot):
        job = _parse_job_detail(sample_snapshot, "https://www.linkedin.com/jobs/view/12345/")
        assert "Lima" in job.location

    def test_extracts_description_up_to_stop_phrase(self, sample_snapshot):
        job = _parse_job_detail(sample_snapshot, "https://www.linkedin.com/jobs/view/12345/")
        assert "pipelines ETL" in job.description
        assert "Snowflake" in job.description
        assert "lunch tickets" in job.description
        assert "Ver empleos similares" not in job.description
        # navigation chrome filtered
        assert "/url:" not in job.description
        assert "Mi red" not in job.description

    def test_no_title_warning(self):
        snap = "Page URL: https://www.linkedin.com/jobs/view/1/\nPage Snapshot\n```yaml\ntext: hi"
        job = _parse_job_detail(snap, "https://www.linkedin.com/jobs/view/12345/")
        assert "title not found" in job.warnings

    def test_no_description_warning(self):
        snap = "Page URL: https://www.linkedin.com/jobs/view/1/\nPage Title: X | LinkedIn\nPage Snapshot\n"
        job = _parse_job_detail(snap, "https://www.linkedin.com/jobs/view/12345/")
        assert "description not found" in job.warnings

    def test_job_id_extracted(self, sample_snapshot):
        url = "https://www.linkedin.com/jobs/view/senior-engineer-1234567/"
        job = _parse_job_detail(sample_snapshot, url)
        assert job.job_id == "1234567"

    def test_extracts_dataclass_returns_dict(self, sample_snapshot):
        job = _parse_job_detail(sample_snapshot, "https://www.linkedin.com/jobs/view/12345/")
        d = job.to_dict()
        assert d["title"] == "Senior Data Engineer"
        assert "company" in d
        assert "warnings" in d

    def test_short_description_is_flagged(self):
        # A near-empty snapshot with a valid Page Title but no description body.
        snap = (
            "Page URL: https://www.linkedin.com/jobs/view/1/\n"
            "Page Title: Dev | Co | LinkedIn\n"
            "Page Snapshot\n```yaml\ntext: Inicio\n```\n"
        )
        job = _parse_job_detail(snap, "https://www.linkedin.com/jobs/view/1/")
        assert job.title == "Dev"
        # description either empty or short → warning issued
        assert any("short" in w or "not found" in w for w in job.warnings)


class TestSavedJobDataclass:
    def test_defaults(self):
        j = SavedJob(title="x", url="https://example.com")
        assert j.title == "x"
        assert j.company == ""
        assert j.warnings == []

    def test_to_dict_round_trip(self):
        j = SavedJob(title="t", url="u", company="c", warnings=["x"])
        d = j.to_dict()
        assert d["title"] == "t"
        assert d["warnings"] == ["x"]