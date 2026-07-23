"""Tests for src/profile/cv_reader.py — HTML parsing (BeautifulSoup4).

The reader consumes `input/base_cv.html` (plain text + hyperlinks, no images)
and produces a CVProfile with the structure the rest of the pipeline expects:
    sections[ {title, kind, entries:[ {titulo, fecha, subtitulo, descriptor,
    enlaces:[ {texto,url} ], bullets:[...] }], table:[[label,value],...] } ]

Critical guarantees tested:
  - The contact line is NOT concatenated with the URLs.
  - The project `<a href>` links are stored as structured {texto,url} objects
    in `entry.enlaces`, never as substrings of `paragraphs`/`bullets`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.profile.cv_reader import (
    CVEntry,
    CVProfile,
    CVSection,
    read_cv,
)


@pytest.fixture
def mini_cv_html(tmp_path: Path) -> Path:
    """Build a small HTML CV mirroring the structure of the real base_cv.html."""
    html = """<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>x</title></head>
<body>
  <div class="header">
    <div class="header-main">
      <p class="name">ALEX SAMPLE CANDIDATE</p>
      <p class="contact-line">
        555 0100 | candidate@example.com |
        <a href="https://example.io/" target="_blank">Sitio</a> |
        <a href="https://github.com/x" target="_blank">Repos</a> |
        Lima, Peru
      </p>
      <p class="tagline">En búsqueda de un puesto en Data Science · Análisis · Visualización</p>
    </div>
  </div>

  <div class="section">
    <p class="section-title">Educación</p>
    <div class="entry-row">
      <span class="entry-title">Example University — Lima</span>
      <span class="entry-date">2021 – 2026</span>
    </div>
    <p class="entry-subtitle">Lic. en Economía | En proceso</p>
  </div>

  <div class="section">
    <p class="section-title">Experiencia Laboral</p>
    <div class="entry-block">
      <div class="entry-row">
        <span class="entry-title">ExampleCorp — Intern</span>
        <span class="entry-date">Nov 2024 – Feb 2025</span>
      </div>
      <ul class="bullets">
        <li>Automaticé los procesos de validación de facturas.</li>
        <li>Extraje y analicé datos de SAP.</li>
      </ul>
    </div>
  </div>

  <div class="section">
    <p class="section-title">Proyectos</p>
    <div class="project-block">
      <div class="project-header">
        <span class="project-title">Rastreador de Gastos Automatizado con IA</span>
        <span class="entry-date">May 2026</span>
      </div>
      <p class="project-links">
        (<a href="https://example-dashboard.example.com/" target="_blank">Dashboard</a>)
      </p>
      <ul class="bullets">
        <li>Desarrollé una automatización que analiza correos bancarios.</li>
        <li>Desarrollé un dashboard interactivo en Streamlit.</li>
      </ul>
    </div>
    <div class="project-block">
      <div class="project-header">
        <span class="project-title">KAYLA</span>
        <span class="entry-date">Jun 2026</span>
      </div>
      <p class="project-links">
        (<a href="https://example-landing.example.com/" target="_blank">Landing Page</a>
        <span class="sep">·</span>
        <a href="https://example-dashboard-2.example.com/" target="_blank">Dashboard</a>)
      </p>
      <ul class="bullets"><li>Un bullet.</li></ul>
    </div>
    <div class="project-block">
      <div class="project-header">
        <span class="project-title">AI Personal Agent</span>
        <span class="entry-date">Jun 2026</span>
      </div>
      <p class="project-links">(Agentic AI · RAG · Automatización)</p>
      <ul class="bullets"><li>Otro bullet.</li></ul>
    </div>
  </div>

  <div class="section">
    <p class="section-title">Habilidades &amp; Herramientas</p>
    <table class="skills-table">
      <tr><td class="skill-label">Programming</td><td>Python, SQL, R, Excel</td></tr>
      <tr><td class="skill-label">Idiomas</td><td>Español, Inglés</td></tr>
    </table>
  </div>
</body></html>
"""
    path = tmp_path / "test_cv.html"
    path.write_text(html, encoding="utf-8")
    return path


class TestReadCV:
    def test_parses_name(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        assert profile.name == "ALEX SAMPLE CANDIDATE"

    def test_parses_contact_visible_text(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        assert "candidate@example.com" in profile.contact
        assert "Lima, Peru" in profile.contact
        # URLs do NOT leak into the contact visible text:
        assert "https://example.io/" not in profile.contact
        assert "https://github.com/x" not in profile.contact

    def test_parses_contact_enlaces_as_structured_objects(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        assert len(profile.contact_enlaces) == 2
        assert profile.contact_enlaces[0].texto == "Sitio"
        assert profile.contact_enlaces[0].url == "https://example.io/"
        assert profile.contact_enlaces[1].texto == "Repos"
        assert profile.contact_enlaces[1].url == "https://github.com/x"

    def test_parses_summary(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        assert profile.summary.startswith("En búsqueda de un puesto en")

    def test_section_kinds(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        kinds = {s.title: s.kind for s in profile.sections}
        assert kinds == {
            "Educación": "educacion",
            "Experiencia Laboral": "experiencia",
            "Proyectos": "proyectos",
            "Habilidades & Herramientas": "habilidades",
        }

    def test_educacion_entries(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        edu = next(s for s in profile.sections if s.title == "Educación")
        assert len(edu.entries) == 1
        assert edu.entries[0].titulo == "Example University — Lima"
        assert edu.entries[0].fecha == "2021 – 2026"
        assert edu.entries[0].subtitulo == "Lic. en Economía | En proceso"
        assert edu.entries[0].bullets == []

    def test_experiencia_entries_with_bullets(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        exp = next(s for s in profile.sections if s.title == "Experiencia Laboral")
        assert len(exp.entries) == 1
        assert exp.entries[0].titulo == "ExampleCorp — Intern"
        assert exp.entries[0].fecha == "Nov 2024 – Feb 2025"
        assert len(exp.entries[0].bullets) == 2
        assert "facturas" in exp.entries[0].bullets[0]
        # No enlaces on experience blocks:
        assert exp.entries[0].enlaces == []

    def test_proyectos_enlaces_preserved_discrete(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        proyectos = next(s for s in profile.sections if s.title == "Proyectos")
        # Three projects in the fixture
        assert len(proyectos.entries) == 3

        proj1 = proyectos.entries[0]
        assert proj1.titulo == "Rastreador de Gastos Automatizado con IA"
        assert proj1.fecha == "May 2026"
        # The descriptor has the parens collapsed (no extra spaces):
        assert proj1.descriptor == "(Dashboard)"
        # The URL is in `enlaces` as a structured object, NOT in the descriptor
        # or bullets:
        assert len(proj1.enlaces) == 1
        assert proj1.enlaces[0].texto == "Dashboard"
        assert proj1.enlaces[0].url == "https://example-dashboard.example.com/"
        bullets_text = " ".join(proj1.bullets)
        assert "https://example-dashboard.example.com/" not in bullets_text
        assert proj1.descriptor.startswith("(") and proj1.descriptor.endswith(")")

    def test_proyectos_multi_link_entry(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        proyectos = next(s for s in profile.sections if s.title == "Proyectos")
        proj2 = proyectos.entries[1]
        assert proj2.titulo == "KAYLA"
        assert len(proj2.enlaces) == 2
        assert proj2.enlaces[0].texto == "Landing Page"
        assert proj2.enlaces[1].texto == "Dashboard"
        assert proj2.descriptor == "(Landing Page · Dashboard)"

    def test_proyectos_no_links_entry(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        proyectos = next(s for s in profile.sections if s.title == "Proyectos")
        proj3 = proyectos.entries[2]
        assert proj3.titulo == "AI Personal Agent"
        # No links; descriptor IS the parenthetical text without URLs:
        assert proj3.enlaces == []
        assert proj3.descriptor == "(Agentic AI · RAG · Automatización)"

    def test_habilidades_projects_table_shape(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        hab = next(s for s in profile.sections if s.title == "Habilidades & Herramientas")
        assert hab.kind == "habilidades"
        assert hab.entries == []
        assert len(hab.table) == 2
        assert hab.table[0] == ["Programming", "Python, SQL, R, Excel"]
        assert hab.table[1] == ["Idiomas", "Español, Inglés"]

    def test_raw_text_contains_all_section_titles(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        for title in ("Educación", "Experiencia Laboral", "Proyectos",
                      "Habilidades & Herramientas"):
            assert title in profile.raw_text

    def test_raw_text_does_not_contain_urls(self, mini_cv_html: Path):
        profile = read_cv(mini_cv_html)
        assert "https://example-dashboard.example.com/" not in profile.raw_text
        assert "https://example.io/" not in profile.raw_text

    def test_to_json_round_trip(self, mini_cv_html: Path, tmp_path: Path):
        import json
        profile = read_cv(mini_cv_html)
        out = tmp_path / "profile.json"
        profile.to_json(out)
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["name"] == profile.name
        assert len(loaded["sections"]) == len(profile.sections)
        # enlaces are in the JSON
        proyectos_dict = next(s for s in loaded["sections"] if s["title"] == "Proyectos")
        first_proj = proyectos_dict["entries"][0]
        assert first_proj["enlaces"][0]["url"] == "https://example-dashboard.example.com/"

    def test_dataclass_defaults(self):
        e = CVEntry()
        assert e.titulo == ""
        assert e.bullets == []
        assert e.enlaces == []
        s = CVSection(title="X")
        assert s.kind == ""
        assert s.entries == []
        assert s.table == []
        assert s.is_empty() is True
        p = CVProfile(name="x", contact="y", contact_enlaces=[], summary="z",
                      sections=[], raw_text="t")
        assert p.name == "x"