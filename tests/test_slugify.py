"""Tests for src/utils/slugify.py"""

from datetime import date

from src.utils.slugify import slugify, job_folder_name


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
        when = date(2026, 7, 13)
        result = job_folder_name("Senior Data Engineer", "Acme", when=when)
        assert result == "2026-07-13_senior-data-engineer_acme"

    def test_includes_company_slug(self):
        when = date(2026, 1, 1)
        result = job_folder_name("Backend", "Globant LLC", when=when)
        assert result.startswith("2026-01-01_backend_globant-llc")

    def test_date_default_today(self):
        result = job_folder_name("X", "Y")
        # YYYY-MM-DD prefix must be present
        assert len(result.split("_")[0]) == 10

    def test_handles_empty_company(self):
        when = date(2026, 7, 13)
        result = job_folder_name("Dev", "", when=when)
        assert result == "2026-07-13_dev_untitled"