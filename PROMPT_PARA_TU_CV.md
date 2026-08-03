# PROMPT_PARA_TU_CV.md — convertí tu CV (PDF/Word/texto) a `input/base_cv.yaml`

Este documento está pensado para pegarlo en **cualquier chat de IA** (ChatGPT,
Claude, Gemini, DeepSeek...) y que esa IA te devuelva tu CV base en YAML,
listo para el pipeline. Si usás Claude Code u Opencode, no lo necesitás: pedile
directamente *"generá mi CV base a partir de este PDF"* y el agente hace la
conversión por vos (ver README).

> **Importante**: el **layout visual** de tu CV original (columnas, íconos,
> foto, colores) **no se conserva**. Solo se conserva el **contenido**. El PDF
> final siempre usa el único template Harvard del repo. Esto es intencional:
> garantiza un look consistente entre todos los usuarios.

---

## Qué hacer

1. Pegá TODO el texto de abajo en tu chat de IA favorito.
2. **Adjuntá o pegá tu CV actual** (PDF, Word, texto plano).
3. La IA te devuelve **solo el YAML**.
4. Guardá ese YAML en `input/base_cv.yaml` dentro del repo.
5. Validá: `python scripts/validate_base_cv.py input/base_cv.yaml`
   (si falla, pedile a la misma IA que corrija los errores y repetí).

---

## Instrucción para la IA (copiar y pegar)

---
Aquí está mi CV actual: [adjuntá o pegá tu CV aquí].

Convertilo al formato YAML de abajo siguiendo **exactamente** el JSON Schema
de `schema/base_cv.schema.json` y el **ejemplo** de `schema/example.yaml`.

Reglas:
- Cada sección real de mi CV mapea a uno de estos 3 tipos:
  - `entry_block`  → Experiencia, Educación, Proyectos, Certificaciones,
    Publicaciones, Voluntariado (estructura "qué / dónde / cuándo / bullets").
  - `simple_list`  → Habilidades, Idiomas, Herramientas, Premios (items sueltos).
  - `text_block`   → Resumen / Perfil / Objetivo (un solo bloque de texto).
- `reorderable: true` solo en secciones donde se puede omitir/reescoger
  entradas por oferta (típicamente Proyectos). El resto va `false` (o omitido).
- Cada bullet/item puede llevar `tags` (2-5 keywords que demuestra), opcional.
- No inventes contenido que no esté en mi CV. Preservá fechas y nombres tal cual.
- Devuelve SOLO el YAML, sin explicaciones, sin comentarios adicionales, sin
  bloques de código markdown.
---

## JSON Schema (schema/base_cv.schema.json)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://auto-tailored-cv.example.com/schema/base_cv.schema.json",
  "title": "base_cv.yaml — contrato de datos",
  "type": "object",
  "required": ["personal_info", "sections"],
  "properties": {
    "personal_info": {
      "type": "object",
      "required": ["name", "email"],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "email": {"type": "string", "minLength": 1},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "links": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["label", "url"],
            "properties": {
              "label": {"type": "string", "minLength": 1},
              "url": {"type": "string", "minLength": 1}
            },
            "additionalProperties": false
          }
        }
      },
      "additionalProperties": false
    },
    "summary": {"type": "string"},
    "sections": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "title", "type"],
        "properties": {
          "id": {"type": "string", "minLength": 1},
          "title": {"type": "string", "minLength": 1},
          "type": {"enum": ["entry_block", "simple_list", "text_block"]},
          "reorderable": {"type": "boolean", "default": false},
          "entries": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "heading": {"type": "string", "minLength": 1},
                "subheading": {"type": "string"},
                "location": {"type": "string"},
                "dates": {"type": "string"},
                "links": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "required": ["label", "url"],
                    "properties": {
                      "label": {"type": "string", "minLength": 1},
                      "url": {"type": "string", "minLength": 1}
                    },
                    "additionalProperties": false
                  }
                },
                "bullets": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                      "text": {"type": "string", "minLength": 1},
                      "tags": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1}
                      }
                    },
                    "additionalProperties": false
                  }
                }
              },
              "additionalProperties": false
            }
          },
          "items": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["text"],
              "properties": {
                "text": {"type": "string", "minLength": 1},
                "tags": {
                  "type": "array",
                  "items": {"type": "string", "minLength": 1}
                }
              },
              "additionalProperties": false
            }
          },
          "text": {"type": "string"}
        },
        "additionalProperties": false,
        "allOf": [
          {
            "if": {"properties": {"type": {"const": "entry_block"}}, "required": ["type"]},
            "then": {
              "required": ["entries"],
              "properties": {
                "items": false,
                "text": false
              }
            }
          },
          {
            "if": {"properties": {"type": {"const": "simple_list"}}, "required": ["type"]},
            "then": {
              "required": ["items"],
              "properties": {
                "entries": false,
                "text": false
              }
            }
          },
          {
            "if": {"properties": {"type": {"const": "text_block"}}, "required": ["type"]},
            "then": {
              "required": ["text"],
              "properties": {
                "entries": false,
                "items": false
              }
            }
          }
        ]
      }
    }
  },
  "additionalProperties": false
}

```

## Ejemplo (schema/example.yaml)

```yaml
# ============================================================================
# Ejemplo de base_cv.yaml — María Fernanda Rojas Castillo (CV ficticio)
#
# Este archivo es la REFERENCIA para generar tu propio `input/base_cv.yaml`.
# Cubre los 3 tipos de sección del schema:
#   - entry_block  : estructura "qué / dónde / cuándo / bullets" (Experiencia,
#                    Educación, Proyectos, Certificaciones, Publicaciones, ...)
#   - simple_list  : lista de items sueltos (Habilidades, Idiomas, Premios, ...)
#   - text_block   : un único bloque de texto (Resumen / Perfil / Objetivo)
#
# `reorderable: true` le da al LLM licencia de reordenar y OMITIR entradas por
# relevancia para cada oferta. Por defecto es `false` (tratamiento estricto 1:1).
#
# El orden de `sections` ES el orden de renderizado del PDF.
# Cada `bullet`/`item` puede llevar `tags` (keywords que demuestra) — opcional,
# habilita el filtrado local pre-LLM.
# ============================================================================

personal_info:
  name: "María Fernanda Rojas Castillo"
  email: "maria.rojas@email.com"
  phone: "+51 900 000 000"
  location: "Lima, Perú"
  links:
    - { label: "Portafolio", url: "https://maria-rojas-portafolio.example.com" }
    - { label: "LinkedIn", url: "https://www.linkedin.com/in/maria-rojas" }

summary: "Perfil profesional orientada a productos digitales, automatización de procesos y análisis de datos, con experiencia práctica en proyectos de impacto medible."

sections:

  # --- text_block: el resumen / perfil --------------------------------
  - id: resumen
    title: "Perfil Profesional"
    type: text_block
    text: "Ingeniera de Sistemas con 3 años de experiencia en transformación digital y automatización de procesos. Combina análisis de datos con diseño de flujos de trabajo para reducir tiempos operativos en más de 40%. Interés en roles de producto, data analytics y automatización."

  # --- entry_block estricto (reorderable: false) -----------------------
  - id: experiencia
    title: "Experiencia Laboral"
    type: entry_block
    reorderable: false
    entries:
      - heading: "Analista de Automatización — TechFlow Perú"
        subheading: "Área de Procesos & Transformación Digital"
        location: "Lima, Perú"
        dates: "Mar 2023 – Actualidad"
        links: []
        bullets:
          - text: "Automaticé el registro contable de facturas en SAP, reduciendo el tiempo de carga de 6 horas a 40 minutos semanales."
            tags: ["automatizacion", "sap", "procesos"]
          - text: "Diseñé dashboards en Power BI para el seguimiento de KPIs operativos usados por la gerencia."
            tags: ["powerbi", "dashboards", "kpis"]
          - text: "Documenté y estandarizó flujos de trabajo en Notion, habilitando la transferencia de conocimiento entre equipos."
            tags: ["documentacion", "procesos"]
      - heading: "Practicante de Inteligencia de Negocios — DataCorp"
        location: "Lima, Perú"
        dates: "Ene 2022 – Feb 2023"
        links: []
        bullets:
          - text: "Elaboré reportes de ventas con Excel y SQL, consolidando fuentes de datos dispersas."
            tags: ["excel", "sql", "reportes"]
          - text: "Apoyé la migración de un proceso manual de conciliación a una planilla automatizada."
            tags: ["automatizacion", "excel"]

  # --- entry_block flexible (reorderable: true) ------------------------
  - id: proyectos
    title: "Proyectos"
    type: entry_block
    reorderable: true
    entries:
      - heading: "BOT de Conciliación Bancaria con IA"
        dates: "2025"
        links:
          - { label: "Dashboard", url: "https://dashboard-bot.example.com" }
          - { label: "Código", url: "https://github.com/maria-rojas/bot-conciliacion" }
        bullets:
          - text: "Automatiza la conciliación de estados de cuenta bancarios mediante una IA que clasifica transacciones."
            tags: ["ia", "automatizacion", "finanzas"]
          - text: "Integra Telegram, Gmail y Google Sheets; despliega un dashboard de seguimiento en Streamlit."
            tags: ["api", "streamlit", "telegram"]
      - heading: "KAYLA — Recordatorios de salud"
        dates: "2025"
        links:
          - { label: "Landing Page", url: "https://kayla.example.com" }
        bullets:
          - text: "Sistema que recuerda citas y recojo de medicamentos a pacientes de postas de salud."
            tags: ["salud", "automatizacion"]
          - text: "Flujo basado en Google Forms + Sheets con envío automático vía bot de Telegram."
            tags: ["gapps", "telegram"]

  # --- entry_block estricto (reorderable: false) -----------------------
  - id: educacion
    title: "Educación"
    type: entry_block
    reorderable: false
    entries:
      - heading: "Universidad Nacional de Ingeniería"
        location: "Lima, Perú"
        dates: "2018 – 2023"
        subheading: "Ingeniería de Sistemas | Egresada"
        links: []
        bullets: []

  # --- simple_list: habilidades / herramientas --------------------------
  - id: habilidades
    title: "Habilidades & Herramientas"
    type: simple_list
    items:
      - text: "Python (Pandas, NumPy, Streamlit, Selenium, APIs)"
        tags: ["python", "data"]
      - text: "SQL y Excel avanzado (Power Query, tablas dinámicas)"
        tags: ["sql", "excel"]
      - text: "Power BI y visualización de datos"
        tags: ["powerbi", "dashboards"]
      - text: "Automatización de procesos (SAP, GApps, APIs)"
        tags: ["automatizacion", "sap"]
      - text: "Gestión de proyectos ágil (Scrum, Jira)"
        tags: ["agil", "scrum"]

  # --- simple_list: idiomas ---------------------------------------------
  - id: idiomas
    title: "Idiomas"
    type: simple_list
    items:
      - text: "Español (nativo)"
      - text: "Inglés (Avanzado — C1)"

```
