"""Tests for src/profile/preferences.py — load_user_preferences (FASE 7.5).

Covers the 4 cases: file absent, file empty, file with only comments, file with
real content.
"""

from __future__ import annotations

from pathlib import Path

from src.profile.preferences import load_user_preferences


class TestLoadUserPreferences:
    def test_absent_file_returns_empty(self, tmp_path: Path):
        assert load_user_preferences(tmp_path / "preferences.txt") == ""

    def test_empty_file_returns_empty(self, tmp_path: Path):
        p = tmp_path / "preferences.txt"
        p.write_text("", encoding="utf-8")
        assert load_user_preferences(p) == ""

    def test_only_comments_and_blank_returns_empty(self, tmp_path: Path):
        p = tmp_path / "preferences.txt"
        p.write_text("\n# comentario\n   \n# otro\n\n", encoding="utf-8")
        assert load_user_preferences(p) == ""

    def test_real_content_kept_comments_stripped(self, tmp_path: Path):
        p = tmp_path / "preferences.txt"
        p.write_text(
            "# cabecera\n"
            "El resumen debe empezar con 'En búsqueda de un puesto en'.\n"
            "\n"
            "# otra regla\n"
            "Bullets de máximo 15 palabras.\n",
            encoding="utf-8",
        )
        result = load_user_preferences(p)
        assert result == (
            "El resumen debe empezar con 'En búsqueda de un puesto en'.\n"
            "Bullets de máximo 15 palabras."
        )
        assert "#" not in result
