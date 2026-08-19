"""Tests for src/render/html_renderer.py — Jinja2 + template (generic schema).

Validates the HTML output:
  - has the right `<head>`/`<body>` shell
  - all rewritable blocks carry `contenteditable="true"` and a unique
    `data-field="..."` attribute
  - hyperlinks from the analysis.json's `links` arrays appear intact as
    `<a href>` tags (URLs protected end-to-end)
  - the save button is injected
  - the shared CSS file is copied next to cv.html
  - the 3 section `type`s (text_block / simple_list / entry_block) render
    consistently, including a non-conventional section name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import settings
from src.render import html_renderer


@pytest.fixture
def base_payload() -> dict:
    """Minimal payload matching the generic schema (analysis.json + base CV
    personal_info header fields)."""
    return {
        "personal_info": {
            "name": "MARÍA FERNANDA ROJAS",
            "email": "maria@example.com",
            "phone": "555 0100",
            "location": "Lima, Perú",
            "links": [
                {"label": "Sitio", "url": "https://example.io/"},
                {"label": "Repos", "url": "https://github.com/x"},
            ],
        },
        "summary": "Perfil orientada a productos digitales y automatización.",
        "sections": [
            {
                "title": "Perfil Profesional",
                "type": "text_block",
                "text": "Ingeniera de Sistemas con experiencia en automatización.",
            },
            {
                "title": "Experiencia Laboral",
                "type": "entry_block",
                "reorderable": False,
                "entries": [
                    {
                        "heading": "Analista de Automatización — TechFlow Perú",
                        "subheading": "Procesos",
                        "location": "Lima",
                        "dates": "Mar 2023 – Actualidad",
                        "links": [
                            {"label": "Proyecto", "url": "https://proyecto.example.com/"}
                        ],
                        "bullets": [
                            {"text": "Bullet 1 sobre SAP.", "tags": ["sap"]},
                            {"text": "Bullet 2 sobre Power BI.", "tags": ["powerbi"]},
                        ],
                    }
                ],
            },
            {
                "title": "Certificaciones",
                "type": "entry_block",
                "reorderable": True,
                "entries": [
                    {
                        "heading": "Microsoft Power BI",
                        "subheading": "",
                        "location": "",
                        "dates": "2024",
                        "links": [],
                        "bullets": [],
                    }
                ],
            },
            {
                "title": "Habilidades & Herramientas",
                "type": "simple_list",
                "items": [
                    {"text": "Python (Pandas, Streamlit)", "tags": ["python"]},
                    {"text": "SQL y Excel avanzado", "tags": ["sql"]},
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
        assert 'data-field="summary"' in html
        assert 'contenteditable="true"' in html

    def test_text_block_rendered_and_editable(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'class="text-block"' in html
        assert "Ingeniera de Sistemas con experiencia en automatización." in html
        assert 'data-field="section.1.text"' in html

    def test_entry_block_renders_all_fields(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert "Analista de Automatización — TechFlow Perú" in html
        assert "Procesos" in html
        assert "Mar 2023 – Actualidad" in html
        # bullets are contenteditable with data-field
        assert 'data-field="section.2.entry.0.bullet.0"' in html
        assert 'data-field="section.2.entry.0.bullet.1"' in html

    def test_non_conventional_section_renders(self, tmp_path: Path, base_payload: dict):
        """A section named 'Certificaciones' renders like any entry_block."""
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert "Certificaciones" in html
        assert "Microsoft Power BI" in html

    def test_simple_list_items_rendered_and_editable(
        self, tmp_path: Path, base_payload: dict
    ):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'class="simple-list"' in html
        assert "Python (Pandas, Streamlit)" in html
        assert "SQL y Excel avanzado" in html
        # items are contenteditable with data-field
        assert 'data-field="section.4.item.0"' in html
        assert 'data-field="section.4.item.1"' in html

    def test_skill_items_render_as_label_and_values_columns(
        self, tmp_path: Path, base_payload: dict
    ):
        base_payload["sections"][3]["items"][0]["text"] = (
            "Programming: Python, SQL, Excel"
        )
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'class="simple-list" data-list-kind="skills"' in html
        assert 'class="skill-label">Programming</span>' in html
        assert 'class="skill-values">Python, SQL, Excel</span>' in html

    def test_entry_links_render_as_anchor_with_url(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'href="https://proyecto.example.com/"' in html
        assert "Proyecto</a>" in html

    def test_save_button_is_present(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'id="save-button"' in html
        assert "guardarYGenerarPDF" in html

    def test_section_title_not_editable(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        for line in html.splitlines():
            if "section-title" in line:
                assert "contenteditable" not in line
                break

    def test_contact_links_reappear_in_html_header(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        assert 'href="https://example.io/"' in html
        assert 'href="https://github.com/x"' in html
        assert "MARÍA FERNANDA ROJAS" in html

    def test_unique_data_field_per_bullet(self, tmp_path: Path, base_payload: dict):
        html = html_renderer.render(base_payload, tmp_path).read_text(encoding="utf-8")
        import re
        fields = re.findall(r'data-field="section\.2\.entry\.(\d+)\.bullet\.(\d+)"', html)
        assert ("0", "0") in fields
        assert ("0", "1") in fields

    def test_unsupported_type_renders_visible_fallback(self, tmp_path: Path):
        payload = {
            "personal_info": {"name": "X", "email": "x@x.com"},
            "summary": "s",
            "sections": [{"title": "Rara", "type": "totally_unknown"}],
        }
        html = html_renderer.render(payload, tmp_path).read_text(encoding="utf-8")
        assert "unsupported-type" in html
        assert "totally_unknown" in html


class TestRenderFromFile:
    def test_renders_from_analysis_json(self, tmp_path: Path, monkeypatch):
        out_dir = tmp_path / "job"
        out_dir.mkdir(parents=True, exist_ok=True)
        analysis = {
            "summary": "Perfil orientada a datos.",
            "sections": [
                {
                    "title": "Perfil Profesional",
                    "type": "text_block",
                    "text": "Perfil con experiencia en análisis.",
                },
            ],
        }
        analysis_path = out_dir / "analysis.json"
        analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

        # Build a fake base_cv.yaml with matching personal_info + section.
        base_yaml = """personal_info:
  name: "MARÍA FERNANDA"
  email: "maria@example.com"
summary: "keep"
sections:
  - id: perfil
    title: "Perfil Profesional"
    type: text_block
    text: "base text"
"""
        base_path = tmp_path / "base_cv.yaml"
        base_path.write_text(base_yaml, encoding="utf-8")

        object.__setattr__(settings, "base_cv_path", base_path)
        try:
            rendered = html_renderer.render_from_file(analysis_path, base_path, out_dir)
            text = rendered.read_text(encoding="utf-8")
            assert 'data-field="summary"' in text
            # personal_info supplemented from base CV
            assert "MARÍA FERNANDA" in text
            assert "Perfil con experiencia en análisis." in text
        finally:
            object.__setattr__(settings, "base_cv_path",
                                Path("input/base_cv.yaml"))
