"""Tests for src/profile/schema_validation.py + scripts/validate_base_cv.py.

FASE 7 items covered here: readable/actionable validation errors (not raw
jsonschema tracebacks), and the standalone script exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.profile.schema_validation import (
    BaseCvValidationError,
    load_schema,
    require_valid_yaml,
    validate_data,
    validate_yaml_file,
)

VALID = {
    "personal_info": {"name": "X", "email": "x@x.com"},
    "sections": [
        {"id": "a", "title": "A", "type": "text_block", "text": "hola"},
        {"id": "b", "title": "B", "type": "entry_block",
         "entries": [{"heading": "H", "dates": "2024", "bullets": [{"text": "t"}]}]},
        {"id": "c", "title": "C", "type": "simple_list",
         "items": [{"text": "i"}]},
    ],
}


class TestValidateData:
    def test_valid_returns_empty(self):
        assert validate_data(VALID) == []

    def test_missing_personal_info_email(self):
        data = json.loads(json.dumps(VALID))
        del data["personal_info"]["email"]
        errors = validate_data(data)
        assert len(errors) == 1
        assert "email" in errors[0]

    def test_unknown_type_is_rejected(self):
        data = json.loads(json.dumps(VALID))
        data["sections"][0]["type"] = "certificaciones"
        errors = validate_data(data)
        assert any("type" in e for e in errors)

    def test_entry_block_requires_entries(self):
        data = json.loads(json.dumps(VALID))
        del data["sections"][1]["entries"]
        errors = validate_data(data)
        assert any("entries" in e and "required" in e for e in errors)

    def test_errors_are_human_readable_paths(self):
        data = json.loads(json.dumps(VALID))
        data["sections"][1]["entries"][0]["bullets"][0]["text"] = ""
        errors = validate_data(data)
        assert any("sections[1]" in e for e in errors)

    def test_error_paths_point_to_section_index(self):
        """FASE 1.3 example: 'sections[2].entries[1]: falta el campo dates'."""
        data = json.loads(json.dumps(VALID))
        # entry_block entry missing 'heading'? heading is not required in the
        # schema (only bullets[].text and personal_info are hard-required), so
        # craft a section with a bullet missing 'text'.
        data["sections"][1]["entries"][0]["bullets"][0] = {"tags": []}
        errors = validate_data(data)
        assert any("sections[1].entries[0].bullets[0]" in e for e in errors)


class TestValidateYamlFile:
    def test_missing_file(self, tmp_path: Path):
        errors = validate_yaml_file(tmp_path / "nope.yaml")
        assert errors and "no existe" in errors[0]

    def test_empty_file(self, tmp_path: Path):
        p = tmp_path / "empty.yaml"
        p.write_text("", encoding="utf-8")
        errors = validate_yaml_file(p)
        assert errors and "vacío" in errors[0]

    def test_valid_yaml(self, tmp_path: Path):
        p = tmp_path / "ok.yaml"
        p.write_text(
            "personal_info:\n  name: X\n  email: x@x.com\nsections: []\n",
            encoding="utf-8",
        )
        assert validate_yaml_file(p) == []


class TestRequireValid:
    def test_raises_with_actionable_message(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("personal_info:\n  name: X\nsections: []\n", encoding="utf-8")
        with pytest.raises(BaseCvValidationError) as exc:
            require_valid_yaml(p)
        assert "email" in str(exc.value)


class TestScript:
    def test_script_accepts_valid(self, tmp_path: Path):
        import subprocess
        import sys
        p = tmp_path / "ok.yaml"
        p.write_text(
            "personal_info:\n  name: X\n  email: x@x.com\nsections: []\n",
            encoding="utf-8",
        )
        r = subprocess.run(
            [sys.executable, "scripts/validate_base_cv.py", str(p)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "OK" in r.stdout

    def test_script_rejects_invalid(self, tmp_path: Path):
        import subprocess
        import sys
        p = tmp_path / "bad.yaml"
        p.write_text("personal_info:\n  name: X\nsections: []\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "scripts/validate_base_cv.py", str(p)],
            capture_output=True, text=True,
        )
        assert r.returncode == 1
        assert "email" in r.stdout


def test_schema_file_loads():
    schema = load_schema()
    assert schema["required"] == ["personal_info", "sections"]
