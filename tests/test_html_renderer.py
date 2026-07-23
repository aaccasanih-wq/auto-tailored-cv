"""Tests for src/render/html_renderer.py — Jinja2 + template.

Validates the HTML output:
  - has the right `<head>`/`<body>` shell
  - all rewritable blocks carry `contenteditable="true"` and a unique
    `data-field="..."` attribute
  - hyperlinks from the analysis.json's `enlaces` arrays appear intact as
    `<a href>` tags (URLs protect end-to-end)
  - the save button is injected
  - the shared CSS file is copied next to cv.html
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import settings
from src.render import html_renderer


@pytest.fixture
def base_payload() -> dict:
    """Minimal payload matching what html_renderer expects (analysis.json +
    base CV header fields)."""
    return {
        "name": "ALEX CANDIDATE",
        "contact": "555 0100 | email@example.com | Sitio | Repos | Lima",
        "contact_enlaces": [
            {"texto": "Sitio", "url": "https://example.io/"},
            {"texto": "Repos", "url": "https://github.com/x"},
        ],
        "summary": "En búsqueda de un puesto en Data Science · Análisis · Visualización",
        "sections": [
            {
                "title": "Educación",
                "kind": "educacion",
                "entries": [
                    {
                        "titulo": "Example University — Lima",
                        "fecha": "2021 – 2026",
                        "subtitulo": "Lic. en Economía | En proceso",
                        "descriptor": "",
                        "enlaces": [],
                        "bullets": [],
                    }
                ],
                "table": [],
            },
            {
                "title": "Experiencia Laboral",
                "kind": "experiencia",
                "entries": [
                    {
                        "titulo": "ExampleCorp — Intern",
                        "fecha": "Nov 2024 – Feb 2025",
                        "subtitulo": "",
                        "descriptor": "",
                        "enlaces": [],
                        "bullets": ["Bullet 1 about Excel.", "Bullet 2 about SAP."],
                    }
                ],
                "table": [],
            },
            {
                "title": "Proyectos",
                "kind": "proyectos",
                "entries": [
                    {
                        "titulo": "Rastreador de Gastos Automatizado con IA",
                        "fecha": "May 2026",
                        "subtitulo": "",
                        "descriptor": "(Dashboard)",
                        "enlaces": [
                            {"texto": "Dashboard", "url": "https://example-dashboard.example.com/"}
                        ],
                        "bullets": ["A bullet", "Another bullet"],
                    }
                ],
                "table": [],
            },
            {
                "title": "Habilidades & Herramientas",
                "kind": "habilidades",
                "entries": [],
                "table": [
                    ["Python", "Pandas, NumPy, Jupyter"],
                    ["Idiomas", "Español, Inglés"],
                ],
            },
        ],
    }


class TestRender:
    def test_writes_cv_html(self, tmp_path: Path, base_payload: dict):
        html_path = html_renderer.render(base_payload, tmp_path)
        assert html_path.exists()
        assert html_path.name == "cv.html"
        text = html_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in text
        assert "<html" in text

    def test_copies_css_to_output(self, tmp_path: Path, base_payload: dict):
        html_path = html_renderer.render(base_payload, tmp_path)
        css_path = html_path.parent / "cv_style.css"
        assert css_path.exists()

    def test_summary_is_contenteditable_with_data_field(
        self, tmp_path: Path, base_payload: dict
    ):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        # The summary <p> must be contenteditable with data-field="summary"
        assert 'data-field="summary"' in html
        assert 'contenteditable="true"' in html

    def test_bullets_are_contenteditable(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        # Each <li> within ul.bullets should be contenteditable with data-field.
        assert 'data-field="section.2.entry.0.bullet.0"' in html
        assert 'data-field="section.3.entry.0.bullet.1"' in html

    def test_skills_table_td_is_contenteditable(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        # row 0 of habilidades table is contenteditable with the right data-field
        assert 'data-field="section.4.table.0"' in html
        assert 'data-field="section.4.table.1"' in html

    def test_project_links_render_as_anchor_with_url_from_enlaces(
        self, tmp_path: Path, base_payload: dict
    ):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        # The URL from entry.enlaces[0].url survives intact in the rendered HTML
        assert 'href="https://example-dashboard.example.com/"' in html
        # The visible link text also renders:
        assert "Dashboard</a>" in html
        # The parenthetical descriptor is rendered as `(<a>Dashboard</a>)` —
        # that is, parens surround the `<a>` tag, and the descriptor is NOT
        # inlined as a separate `(Dashboard)` plain text node next to the link.
        import re
        m = re.search(r"<p class=\"project-links\">([^<]*<a[^>]*>Dashboard</a>[^<]*)</p>",
                       html)
        assert m is not None, "project-links paragraph not found"
        chunk = m.group(1).strip()
        assert chunk.startswith("(")
        assert chunk.endswith(")")
        # The URL inside the anchor matches the analysis.json enlaces URL:
        assert 'href="https://example-dashboard.example.com/"' in chunk

    def test_save_button_is_present(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'id="save-button"' in html
        # The onclick handler is wired
        assert "guardarYGenerarPDF" in html

    def test_immutable_titles_rendered_not_editable(
        self, tmp_path: Path, base_payload: dict
    ):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        # The section-title (Educación) is plain <p>, NOT contenteditable
        # We assert it contains the title text and that the line with
        # section-title does not have contenteditable.
        for line in html.splitlines():
            if "section-title" in line:
                assert "contenteditable" not in line
                break

    def test_contact_links_reappear_in_html_header(
        self, tmp_path: Path, base_payload: dict
    ):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        # contact_enlaces URLs are emitted as <a href> in the contact-line:
        assert 'href="https://example.io/"' in html
        assert 'href="https://github.com/x"' in html

    def test_unique_data_field_per_bullet(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        import re
        fields = re.findall(r'data-field="section\.2\.entry\.(\d+)\.bullet\.(\d+)"', html)
        # Two bullets for entry 0 → fields (("0","0"), ("0","1"))
        assert ("0", "0") in fields
        assert ("0", "1") in fields


class TestRenderFromFile:
    def test_renders_from_analysis_json(self, tmp_path: Path, monkeypatch):
        # Build a base_cv.html fixture and an analysis.json next to it.
        out_dir = tmp_path / "job"
        out_dir.mkdir(parents=True, exist_ok=True)
        analysis = {
            "summary": "En búsqueda de un puesto en X · Y · Z",
            "sections": [
                {
                    "title": "Educación",
                    "kind": "educacion",
                    "entries": [
                        {"titulo": "U", "fecha": "2021 – 2026",
                         "subtitulo": "", "descriptor": "", "bullets": []}
                    ],
                    "table": [],
                },
            ],
        }
        analysis_path = out_dir / "analysis.json"
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

        # Build a fake base_cv.html with a matching header + section.
        base_html = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>x</title></head>
<body>
<div class="header">
  <div class="header-main">
    <p class="name">ALEX</p>
    <p class="contact-line">email@example</p>
    <p class="tagline">Keep summary</p>
  </div>
</div>
<div class="section">
  <p class="section-title">Educación</p>
  <div class="entry-row">
    <span class="entry-title">U</span>
    <span class="entry-date">2021 – 2026</span>
  </div>
</div>
</body></html>"""
        base_path = tmp_path / "base_cv.html"
        base_path.write_text(base_html, encoding="utf-8")

        # Make html_renderer use this base path via settings.
        object.__setattr__(settings, "base_cv_path", base_path)
        try:
            rendered = html_renderer.render_from_file(analysis_path, base_path, out_dir)
            text = rendered.read_text(encoding="utf-8")
            assert 'data-field="summary"' in text
            # section kind propagated from base (since analysis had no 'kind')
            assert "Educación" in text
        finally:
            object.__setattr__(settings, "base_cv_path",
                                Path("input/base_cv.html"))