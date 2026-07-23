"""Tests for src/utils/slugify.py"""

from datetime import date
from pathlib import Path

from src.utils.slugify import job_folder_name, job_output_path, slugify


class TestSlugify:
    def test_simple_english(self):
        assert slugify("Senior Data Engineer") == "senior-data-engineer"

    def test_with_punctuation(self):
        assert slugify("Senior Data Engineer (Remote)") == "senior-data-engineer-remote"

    def test_strips_accents(self):
        assert slugify("Ingeniero de Datos Sénior") == "ingeniero-de-datos-senior"

    def test_strips_emojis_and_unicode(self):
        assert slugify("Frontend Dev 🚀 Tokyo") == "frontend-dev-tokyo"

    def test_collapses_multiple_dashes(self):
        assert slugify("a---b   c") == "a-b-c"

    def test_trims_leading_trailing_dashes(self):
        assert slugify("--- leading and trailing ---") == "leading-and-trailing"

    def test_empty_input(self):
        assert slugify("") == "untitled"

    def test_all_non_ascii_input(self):
        assert slugify("中文日本語") == "untitled"

    def test_max_length(self):
        result = slugify("a" * 200, max_length=10)
        assert len(result) <= 10
        assert result == "aaaaaaaaaa"


class TestJobFolderName:
    def test_basic(self):
        result = job_folder_name("Senior Data Engineer", "Acme")
        assert result == "senior-data-engineer_acme"

    def test_includes_company_slug(self):
        result = job_folder_name("Backend", "Globant LLC")
        assert result.startswith("backend_globant-llc")

    def test_ignores_when_arg(self):
        # job_folder_name no longer prefixes the date; when is accepted for
        # backward-compat but does not affect the result.
        a = job_folder_name("X", "Y", when=date(2026, 1, 1))
        b = job_folder_name("X", "Y", when=date(2026, 7, 13))
        assert a == b == "x_y"

    def test_handles_empty_company(self):
        result = job_folder_name("Dev", "")
        assert result == "dev_untitled"


class TestJobOutputPath:
    def test_nests_under_date_dir(self):
        out = Path("output")
        p = job_output_path(out, "Senior Data Engineer", "Acme", when=date(2026, 7, 13))
        assert p == Path("output") / "2026-07-13" / "senior-data-engineer_acme"

    def test_defaults_to_today(self):
        p = job_output_path(Path("output"), "X", "Y")
        # Last component is the job folder, parent is a YYYY-MM-DD dir.
        assert p.parent.parent == Path("output")
        assert len(p.parent.name.split("-")) == 3

    def test_handles_empty_company(self):
        p = job_output_path(Path("output"), "Dev", "", when=date(2026, 7, 13))
        assert p.name == "dev_untitled"

    def test_accepts_str_output_dir(self):
        p = job_output_path("output", "Dev", "Acme", when=date(2026, 7, 13))
        assert p == Path("output") / "2026-07-13" / "dev_acme"