"""Tests for src/render/pdf_converter.py — uses subprocess mocking."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.render.legacy import pdf_converter
from src.render.legacy.pdf_converter import (
    ConversionResult,
    convert_docx_to_pdf,
    find_soffice,
)


class TestFindSoffice:
    def test_explicit_executable_existing(self, tmp_path: Path):
        fake = tmp_path / "soffice"
        fake.write_text("#!/bin/bash\necho ok", encoding="utf-8")
        fake.chmod(0o755)
        assert find_soffice(str(fake)) == str(fake)

    def test_explicit_executable_missing(self):
        assert find_soffice("/no/such/soffice") is None

    def test_default_so6ffice_falls_back_to_path(self, monkeypatch):
        # Pretend shutil.which finds it on PATH
        monkeypatch.setattr(pdf_converter.shutil, "which", lambda name: "/usr/local/bin/soffice")
        assert find_soffice("soffice") == "/usr/local/bin/soffice"

    def test_returns_none_when_not_found(self, monkeypatch):
        monkeypatch.setattr(pdf_converter.shutil, "which", lambda name: None)
        # And all fallback paths don't exist:
        monkeypatch.setattr(pdf_converter, "MACOS_SOFFICE_PATHS", ())
        assert find_soffice("soffice") is None


class TestConvertDocxToPdf:
    def test_missing_input_returns_error(self, tmp_path: Path):
        result = convert_docx_to_pdf(tmp_path / "no.docx", tmp_path)
        assert isinstance(result, ConversionResult)
        assert result.success is False
        assert "not found" in result.error

    def test_no_soffice_returns_error(self, tmp_path: Path, monkeypatch):
        docx = tmp_path / "in.docx"
        docx.write_bytes(b"PK\x03\x04")  # minimal docx signature bytes; not real but file exists
        monkeypatch.setattr(pdf_converter, "find_soffice", lambda executable: None)
        result = convert_docx_to_pdf(docx, tmp_path)
        assert result.success is False
        assert "LibreOffice" in result.error

    def test_successful_conversion_writes_pdf(self, tmp_path: Path, monkeypatch):
        docx = tmp_path / "in.docx"
        docx.write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(pdf_converter, "find_soffice", lambda executable: "/usr/local/bin/soffice")

        def fake_run(cmd, *args, **kwargs):
            # Determine the output path the command would have produced
            # cmd: [soffice, ..., "--outdir", <out>, <input.docx>]
            for i, token in enumerate(cmd):
                if token == "--outdir" and i + 1 < len(cmd):
                    out_dir = Path(cmd[i + 1])
                    name = Path(cmd[-1]).stem + ".pdf"
                    (out_dir / name).write_bytes(b"%PDF-1.4 fake")
                    break
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(pdf_converter.subprocess, "run", fake_run)
        result = convert_docx_to_pdf(docx, tmp_path, timeout_seconds=5)
        assert result.success is True
        assert result.pdf_path is not None
        assert result.pdf_path.exists()
        assert result.pdf_path.suffix == ".pdf"

    def test_soffice_nonzero_exit_is_failure(self, tmp_path: Path, monkeypatch):
        docx = tmp_path / "in.docx"
        docx.write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(pdf_converter, "find_soffice", lambda executable: "/usr/local/bin/soffice")
        monkeypatch.setattr(
            pdf_converter.subprocess,
            "run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="bad input"),
        )
        result = convert_docx_to_pdf(docx, tmp_path, timeout_seconds=5)
        assert result.success is False
        assert "bad input" in result.error

    def test_timeout_is_failure(self, tmp_path: Path, monkeypatch):
        docx = tmp_path / "in.docx"
        docx.write_bytes(b"PK\x03\x04")
        monkeypatch.setattr(pdf_converter, "find_soffice", lambda executable: "/usr/local/bin/soffice")
        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=1)
        monkeypatch.setattr(pdf_converter.subprocess, "run", boom)
        result = convert_docx_to_pdf(docx, tmp_path, timeout_seconds=5)
        assert result.success is False
        assert "timeout" in result.error