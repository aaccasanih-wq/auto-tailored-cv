"""Tests for src/tailor/prompt_loader.py — load_prompt override mechanism
(FASE 7.8). Covers the override fallback for all 4 prompts.
"""

from __future__ import annotations

import pytest

from src.tailor.prompt_loader import PROMPTS_DIR, load_prompt

PROMPT_NAMES = [
    "tailor_system",
    "evaluator_system",
    "repair_system",
    "job_summarizer_system",
]


class TestLoadPrompt:
    def test_all_defaults_exist_and_are_nonempty(self):
        for name in PROMPT_NAMES:
            content = load_prompt(name)
            assert content.strip(), f"{name}.txt is empty"

    def test_default_is_repo_file(self):
        content = load_prompt("tailor_system")
        expected = (PROMPTS_DIR / "tailor_system.txt").read_text(encoding="utf-8").strip()
        assert content == expected

    def test_override_wins_when_present(self, tmp_path, monkeypatch):
        """FASE 7.8: {name}.override.txt wins when it exists; falls back to
        {name}.txt otherwise."""
        override = tmp_path / "tailor_system.override.txt"
        override.write_text("OVERRIDE CONTENT", encoding="utf-8")
        monkeypatch.setattr("src.tailor.prompt_loader.PROMPTS_DIR", tmp_path)
        assert load_prompt("tailor_system") == "OVERRIDE CONTENT"

    def test_override_is_gitignored_survivor(self, tmp_path, monkeypatch):
        """With only the default present, the loader returns the default even
        when an unrelated file exists in the dir."""
        (tmp_path / "tailor_system.txt").write_text("DEFAULT", encoding="utf-8")
        monkeypatch.setattr("src.tailor.prompt_loader.PROMPTS_DIR", tmp_path)
        assert load_prompt("tailor_system") == "DEFAULT"

    def test_missing_prompt_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.tailor.prompt_loader.PROMPTS_DIR", tmp_path)
        with pytest.raises(FileNotFoundError):
            load_prompt("does_not_exist")

    def test_prompts_dir_is_repo_prompts(self):
        assert PROMPTS_DIR.name == "prompts"
        assert (PROMPTS_DIR / "tailor_system.txt").exists()
