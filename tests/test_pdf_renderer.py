"""Tests for src/render/pdf_renderer.py — Playwright-based HTML→PDF.

Playwright + Chromium is needed at runtime; if no Chromium build exists for
this machine, all tests are SKIPPED (not failed) — that's the contract for
"slow / optional test" per OPENCODE_INSTRUCTIONS section 7.

We mark the full-fixture test with `@pytest.mark.slow` so CI can opt out, and
we never attempt Chromium download beyond the `install` the user already ran.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.render import pdf_renderer

_ISR_PLAYWRIGHT_AVAILABLE = None


def _playwright_available() -> bool:
    global _ISR_PLAYWRIGHT_AVAILABLE
    if _ISR_PLAYWRIGHT_AVAILABLE is None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
            _ISR_PLAYWRIGHT_AVAILABLE = True
        except Exception:
            _ISR_PLAYWRIGHT_AVAILABLE = False
    return _ISR_PLAYWRIGHT_AVAILABLE


pytestmark = pytest.mark.skipif(
    not _playwright_available(),
    reason="Playwright + Chromium not installed (run `python3 -m playwright install chromium`).",
)


@pytest.fixture
def fixture_html(tmp_path: Path) -> Path:
    html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>fixture</title>
<style>
  @page { size: Letter; margin: 0.5in; }
  body { font-family: Arial; font-size: 11pt; }
</style></head>
<body><h1>Fixture CV</h1><p>Hello from fixture.</p></body></html>"""
    path = tmp_path / "cv.html"
    path.write_text(html, encoding="utf-8")
    return path


class TestRender:
    def test_missing_input_returns_error(self, tmp_path: Path):
        result = pdf_renderer.render(tmp_path / "nope.html")
        assert result.success is False
        assert "not found" in (result.error or "")

    def test_renders_simple_html_to_pdf(self, fixture_html: Path):
        result = pdf_renderer.render(fixture_html)
        assert result.success is True
        assert result.pdf_path is not None
        assert result.pdf_path.exists()
        assert result.pdf_path.stat().st_size > 200  # a real PDF
        # Head of every PDF file is `%PDF-`
        head = result.pdf_path.read_bytes()[:5]
        assert head.startswith(b"%PDF")

    def test_pdf_named_after_html_stem(self, fixture_html: Path):
        result = pdf_renderer.render(fixture_html)
        assert result.pdf_path is not None
        assert result.pdf_path.name == "cv.pdf"