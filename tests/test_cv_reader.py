"""Tests for src/profile/cv_reader.py — YAML parsing.

The reader consumes `input/base_cv.yaml` (validated against
`schema/base_cv.schema.json`) and produces a CVProfile:

    personal_info {name, email, phone, location, links:[{label,url}]}
    summary
    sections[ {id, title, type: entry_block|simple_list|text_block,
               reorderable, entries[], items[], text} ]

Critical guarantees tested:
  - Any section type is parsed generically (no 4-kind hardcoding): a section
    called "Certificaciones" parses like any entry_block.
  - An unknown `type` or an invalid file raises an explicit exception — never
    a silent drop.
  - Protected links are captured as structured {label,url} objects and never
    appear in `raw_text`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.profile.cv_reader import (
    CVBullet,
    CVEntry,
    CVItem,
    CVProfile,
    CVSection,
    Enlace,
    PersonalInfo,
    read_cv,
)
from src.profile.schema_validation import BaseCvValidationError

VALID_YAML = """\
personal_info:
  name: "María Fernanda Rojas Castillo"
  email: "maria.rojas@email.com"
  phone: "+51 900 000 000"
  location: "Lima, Perú"
  links:
    - { label: "Portafolio", url: "https://portafolio.example.com" }
    - { label: "LinkedIn", url: "https://www.linkedin.com/in/maria" }

summary: "Perfil orientada a productos digitales."

sections:
  - id: perfil
    title: "Perfil Profesional"
    type: text_block
    text: "Ingeniera de Sistemas con experiencia en automatización."

  - id: experiencia
    title: "Experiencia Laboral"
    type: entry_block
    reorderable: false
    entries:
      - heading: "Analista de Automatización — TechFlow Perú"
        subheading: "Procesos & Transformación"
        location: "Lima, Perú"
        dates: "Mar 2023 – Actualidad"
        links:
          - { label: "Proyecto", url: "https://example.com/proyecto" }
        bullets:
          - text: "Automaticé el registro contable en SAP."
            tags: ["automatizacion", "sap"]
          - text: "Diseñé dashboards en Power BI."
            tags: ["powerbi"]

  - id: certificaciones
    title: "Certificaciones"
    type: entry_block
    reorderable: true
    entries:
      - heading: "Microsoft Power BI Data Analyst"
        dates: "2024"
        links: []
        bullets: []

  - id: habilidades
    title: "Habilidades & Herramientas"
    type: simple_list
    items:
      - text: "Python (Pandas, Streamlit, APIs)"
        tags: ["python"]
      - text: "SQL y Excel avanzado"
        tags: ["sql"]
"""


@pytest.fixture
def mini_cv_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "base_cv.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    return path


class TestReadCV:
    def test_parses_personal_info(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        assert profile.personal_info.name == "María Fernanda Rojas Castillo"
        assert profile.personal_info.email == "maria.rojas@email.com"
        assert profile.personal_info.phone == "+51 900 000 000"
        assert profile.personal_info.location == "Lima, Perú"
        assert [link.label for link in profile.personal_info.links] == ["Portafolio", "LinkedIn"]
        assert profile.personal_info.links[0].url == "https://portafolio.example.com"

    def test_parses_summary(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        assert profile.summary == "Perfil orientada a productos digitales."

    def test_section_types(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        types = {s.title: s.type for s in profile.sections}
        assert types == {
            "Perfil Profesional": "text_block",
            "Experiencia Laboral": "entry_block",
            "Certificaciones": "entry_block",
            "Habilidades & Herramientas": "simple_list",
        }

    def test_reorderable_flags(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        flags = {s.title: s.reorderable for s in profile.sections}
        assert flags["Experiencia Laboral"] is False
        assert flags["Certificaciones"] is True  # non-conventional, flexible

    def test_entry_block_parsed_generically(self, mini_cv_yaml: Path):
        """A section called 'Certificaciones' parses like any entry_block — it
        is NOT dropped or misclassified."""
        profile = read_cv(mini_cv_yaml)
        certs = next(s for s in profile.sections if s.title == "Certificaciones")
        assert len(certs.entries) == 1
        assert certs.entries[0].heading == "Microsoft Power BI Data Analyst"
        assert certs.entries[0].dates == "2024"

    def test_entry_fields_with_links_and_bullets(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        exp = next(s for s in profile.sections if s.title == "Experiencia Laboral")
        e = exp.entries[0]
        assert e.heading == "Analista de Automatización — TechFlow Perú"
        assert e.subheading == "Procesos & Transformación"
        assert e.location == "Lima, Perú"
        assert e.dates == "Mar 2023 – Actualidad"
        # protected links are structured objects
        assert [link.label for link in e.links] == ["Proyecto"]
        assert e.links[0].url == "https://example.com/proyecto"
        # bullets carry text + tags
        assert len(e.bullets) == 2
        assert e.bullets[0].text == "Automaticé el registro contable en SAP."
        assert e.bullets[0].tags == ["automatizacion", "sap"]

    def test_simple_list_items(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        hab = next(s for s in profile.sections if s.title == "Habilidades & Herramientas")
        assert len(hab.items) == 2
        assert hab.items[0].text == "Python (Pandas, Streamlit, APIs)"
        assert hab.items[0].tags == ["python"]

    def test_text_block_text(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        perfil = next(s for s in profile.sections if s.title == "Perfil Profesional")
        assert perfil.text == "Ingeniera de Sistemas con experiencia en automatización."

    def test_raw_text_contains_titles_but_not_urls(self, mini_cv_yaml: Path):
        profile = read_cv(mini_cv_yaml)
        for title in ("Experiencia Laboral", "Certificaciones", "Habilidades & Herramientas"):
            assert title in profile.raw_text
        assert "https://example.com/proyecto" not in profile.raw_text
        assert "https://portafolio.example.com" not in profile.raw_text

    def test_invalid_type_raises(self, tmp_path: Path):
        bad = VALID_YAML.replace("type: text_block", "type: something_else")
        path = tmp_path / "bad.yaml"
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(BaseCvValidationError):
            read_cv(path)

    def test_missing_required_field_raises(self, tmp_path: Path):
        bad = VALID_YAML.replace('    type: text_block\n    text: "Ingeniera de Sistemas con experiencia en automatización."', "    type: text_block\n")
        path = tmp_path / "bad.yaml"
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(BaseCvValidationError):
            read_cv(path)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            read_cv(tmp_path / "nope.yaml")

    def test_dataclass_defaults(self):
        e = CVEntry()
        assert e.heading == ""
        assert e.bullets == []
        s = CVSection(title="X")
        assert s.type == ""
        assert s.reorderable is False
        assert s.entries == []
        p = CVProfile(personal_info=PersonalInfo(name="x", email="e"))
        assert p.personal_info.name == "x"
        b = CVBullet(text="t")
        i = CVItem(text="i")
        link = Enlace(label="l", url="u")
        assert b.to_dict() == {"text": "t", "tags": []}
        assert i.to_dict() == {"text": "i", "tags": []}
        assert link.to_dict() == {"label": "l", "url": "u"}
