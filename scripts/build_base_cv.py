#!/usr/bin/env python3
"""Generate a starter `input/base_cv.yaml` with generic placeholders.

This replaces the old script that produced a `.docx` with one person's real
data hardcoded. The YAML template validates against
`schema/base_cv.schema.json` out of the box and serves as a scaffold: replace
the placeholder values with your own data (or better, let your coding agent /
PROMPT_PARA_TU_CV.md convert your real PDF/Word CV into this format).

Usage:
    python scripts/build_base_cv.py                  # write input/base_cv.yaml
    python scripts/build_base_cv.py --path custom.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.profile.schema_validation import validate_yaml_file  # noqa: E402

TEMPLATE = """\
# ----------------------------------------------------------------------------
# input/base_cv.yaml — plantilla generada por scripts/build_base_cv.py
#
# Reemplazá los placeholders con tus datos reales y validá con:
#     python scripts/validate_base_cv.py input/base_cv.yaml
#
# Los 3 tipos de sección:
#   entry_block  → Experiencia, Educación, Proyectos, Certificaciones, ...
#   simple_list  → Habilidades, Idiomas, Herramientas, Premios
#   text_block   → Resumen / Perfil / Objetivo
# `reorderable: true` permite al LLM reordenar/omitir entradas por oferta.
# ----------------------------------------------------------------------------

personal_info:
  name: "Nombre Apellido"
  email: "email@ejemplo.com"
  phone: "+51 900 000 000"
  location: "Lima, Perú"
  links:
    - { label: "LinkedIn", url: "https://www.linkedin.com/in/ejemplo" }

summary: "Breve resumen profesional (1-2 líneas)."

sections:
  - id: resumen
    title: "Perfil Profesional"
    type: text_block
    text: "Escribí aquí un resumen de tu perfil profesional."

  - id: experiencia
    title: "Experiencia Laboral"
    type: entry_block
    reorderable: false
    entries:
      - heading: "Puesto — Empresa"
        subheading: "Área / departamento"
        location: "Ciudad, País"
        dates: "Ene 2023 – Actualidad"
        links: []
        bullets:
          - text: "Logro medible y concreto."
            tags: ["palabra_clave_1"]
          - text: "Otro logro relevante."
            tags: []

  - id: educacion
    title: "Educación"
    type: entry_block
    reorderable: false
    entries:
      - heading: "Universidad"
        location: "Ciudad, País"
        dates: "2018 – 2023"
        subheading: "Título | Estado"
        links: []
        bullets: []

  - id: proyectos
    title: "Proyectos"
    type: entry_block
    reorderable: true
    entries:
      - heading: "Nombre del Proyecto"
        dates: "2025"
        links:
          - { label: "Código", url: "https://github.com/ejemplo/proyecto" }
        bullets:
          - text: "Qué hace / qué problema resuelve el proyecto."
            tags: ["tecnologia"]
          - text: "Detalle de implementación."
            tags: []

  - id: habilidades
    title: "Habilidades & Herramientas"
    type: simple_list
    items:
      - text: "Python (Pandas, Streamlit, APIs)"
        tags: ["python"]
      - text: "SQL y Excel avanzado"
        tags: ["sql", "excel"]

  - id: idiomas
    title: "Idiomas"
    type: simple_list
    items:
      - text: "Español (nativo)"
      - text: "Inglés (intermedio)"
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_base_cv.py")
    parser.add_argument("--path", default=str(_ROOT / "input" / "base_cv.yaml"),
                        help="ruta de salida (default: input/base_cv.yaml)")
    args = parser.parse_args(argv)

    out = Path(args.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE, encoding="utf-8")

    errors = validate_yaml_file(out)
    if errors:
        print(f"ERROR: la plantilla generada no valida:\n" + "\n".join(f"  - {e}" for e in errors))
        return 1
    print(f"Escribí la plantilla en {out}. Validación OK.")
    print("Reemplazá los placeholders con tus datos y volvé a validar:")
    print(f"  python scripts/validate_base_cv.py {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
