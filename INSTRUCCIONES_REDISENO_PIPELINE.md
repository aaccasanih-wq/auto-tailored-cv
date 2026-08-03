# Instrucciones de rediseño — auto-tailored-cv

Este documento consolida todos los cambios a implementar para que el pipeline deje de estar acoplado al CV de un usuario específico (Axel) y funcione de forma genérica para cualquier persona que quiera publicar/usar el proyecto.

Está organizado en fases. Cada fase es implementable y verificable de forma independiente; seguir el orden reduce el riesgo de romper el pipeline a mitad de camino.

---

## FASE 0 — Contexto y objetivo

El pipeline actual (`extract → tailor → evaluate → repair → render`) funciona, pero tiene tres problemas para hacerlo público:

1. **El CV base y el parser están acoplados a la estructura exacta del CV de Axel** (4 tipos de sección hardcodeados: `educacion`/`experiencia`/`proyectos`/`habilidades`, detectados por nombre de clase CSS). Un usuario con secciones distintas (Certificaciones, Publicaciones, Idiomas) pierde esas secciones **en silencio**.
2. **Los system prompts hardcodean ese mismo enum** de 4 tipos, más una regla de formato de resumen específica de Axel ("En búsqueda de un puesto en...").
3. **El consumo de tokens es alto** (~134k tokens por CV generado desde cero) porque la descripción cruda de la oferta se reenvía completa en las 3 pasadas del LLM, y no hay visibilidad de dónde se gasta.

El objetivo final: cualquier persona debe poder generar su `base_cv.yaml` (con ayuda de cualquier IA externa + el schema del repo), correr el pipeline sin tocar código ni prompts, y obtener un PDF con el mismo estilo Harvard consistente que el de cualquier otro usuario.

Dos objetivos adicionales incorporados en esta versión: **(a)** los system prompts deben ser observables y editables por el propio usuario, sin depender de leer código Python; **(b)** el flujo debe funcionar de punta a punta para alguien que nunca usó una terminal, cuyo único insumo es su CV actual en PDF o Word.

---

## FASE 1 — Formato del CV base: migrar de HTML a YAML con schema

### 1.1 Crear `schema/base_cv.schema.json`

JSON Schema que define el contrato de datos. Estructura de alto nivel:

```json
{
  "type": "object",
  "required": ["personal_info", "sections"],
  "properties": {
    "personal_info": {
      "type": "object",
      "required": ["name", "email"],
      "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "links": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["label", "url"],
            "properties": {
              "label": {"type": "string"},
              "url": {"type": "string"}
            }
          }
        }
      }
    },
    "summary": {"type": "string"},
    "sections": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "title", "type"],
        "properties": {
          "id": {"type": "string"},
          "title": {"type": "string"},
          "type": {"enum": ["entry_block", "simple_list", "text_block"]},
          "reorderable": {"type": "boolean", "default": false},
          "entries": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "heading": {"type": "string"},
                "subheading": {"type": "string"},
                "location": {"type": "string"},
                "dates": {"type": "string"},
                "links": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "url": {"type": "string"}}
                  }
                },
                "bullets": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "text": {"type": "string"},
                      "tags": {"type": "array", "items": {"type": "string"}}
                    }
                  }
                }
              }
            }
          },
          "items": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "text": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}}
              }
            }
          },
          "text": {"type": "string"}
        }
      }
    }
  }
}
```

Notas de diseño (mantener al implementar):
- `type: entry_block` usa el campo `entries` (Experiencia, Educación, Proyectos, Certificaciones, Publicaciones, Voluntariado — cualquier cosa con estructura "qué / dónde / cuándo / bullets").
- `type: simple_list` usa el campo `items` (Habilidades, Idiomas, Herramientas, Premios).
- `type: text_block` usa el campo `text` (Resumen/Perfil).
- `reorderable: true` en una sección le da al LLM licencia de reordenar y omitir entradas por relevancia (hoy esto solo aplica, hardcodeado, a "proyectos"). Por defecto `false` (tratamiento estricto 1:1).
- Cada `bullet` puede llevar `tags` opcionales (keywords que ese bullet demuestra) — esto habilita el filtrado local pre-LLM de la Fase 4.
- El orden de `sections` en el array **es** el orden de renderizado. No hay campo `order` aparte.

### 1.2 Crear `schema/example.yaml`

Un CV de ejemplo completo y realista (puede reusarse el CV ficticio "María Fernanda Rojas Castillo" ya generado en esta conversación, adaptado al nuevo schema), cubriendo los 3 `type` y al menos una sección `reorderable: true` y una `reorderable: false`, para que sirva de referencia clara tanto a usuarios humanos como a las IAs que generarán sus archivos.

### 1.3 Crear `scripts/validate_base_cv.py`

Script standalone que valida un YAML contra el schema sin tocar el resto del pipeline ni requerir LLM ni login de LinkedIn:

```bash
python scripts/validate_base_cv.py input/base_cv.yaml
```

Debe imprimir errores legibles y accionables (ej. "sections[2].entries[1]: falta el campo 'dates'"), no solo el traceback crudo de `jsonschema`.

### 1.4 Crear `PROMPT_PARA_TU_CV.md`

Documento listo para copiar-pegar en cualquier IA externa (ChatGPT, Claude, Gemini, etc.), que incluya:
- El contenido de `schema/base_cv.schema.json`.
- El contenido de `schema/example.yaml`.
- Una instrucción clara: *"Aquí está mi CV actual [el usuario adjunta su PDF/Word/texto]. Conviértelo a YAML siguiendo exactamente este schema y este ejemplo. Devuelve SOLO el YAML, sin explicaciones ni comentarios adicionales."*

### 1.5 Reescribir `src/profile/cv_reader.py`

- Eliminar `_detect_kind()` basado en clases CSS (`project-block`, `entry-block`, `skills-table`, `entry-row`).
- Nuevo lector que parsea YAML (usando `pyyaml`), valida contra el schema (reusar la lógica de `scripts/validate_base_cv.py`), y construye `CVProfile`/`CVSection`/`CVEntry` con:
  - `section.type` en vez de `section.kind`, con los 3 valores fijos.
  - `section.reorderable: bool`.
  - Si una sección tiene un `type` no reconocido o el archivo no valida contra el schema, **debe fallar con una excepción explícita y un mensaje accionable** — nunca descartar la sección en silencio (comportamiento actual de `is_empty()`, que hay que eliminar).
- Actualizar `BASE_CV_PATH` por defecto en `.env.example` y `src/config.py` a `input/base_cv.yaml`.

### 1.6 Actualizar `.gitignore`

Cambiar la entrada de `input/base_cv.html` por `input/base_cv.yaml` (sigue sin subirse al repo — dato personal de cada usuario).

### 1.7 Reconocer la conversión PDF/Word → YAML como tarea del skill/`AGENTS.md`

La mayoría de usuarios nuevos va a llegar con su CV en PDF o Word, no en YAML — no se les puede pedir que lo reescriban a mano. El proyecto debe soportar explícitamente que el mismo agente de código que el usuario ya tiene instalado (Claude Code / Opencode, Opción A del README) haga esa conversión por él, en un solo pedido en lenguaje natural.

- Actualizar el skill instalado (`~/.claude/skills/` / `~/.config/opencode/skills/`, ver `scripts/install_skill.sh`) y/o `AGENTS.md` para que reconozcan explícitamente instrucciones equivalentes a *"convertime mi CV a base_cv.yaml"* / *"generá mi CV base a partir de este PDF"* como una tarea soportada del proyecto, con este procedimiento:
  1. Leer `schema/base_cv.schema.json` y `schema/example.yaml`.
  2. Leer el PDF/Word que el usuario adjuntó y mapear cada sección real a `entry_block`/`simple_list`/`text_block` según corresponda (ver tabla de equivalencias abajo).
  3. Escribir `input/base_cv.yaml`.
  4. Ejecutar `python scripts/validate_base_cv.py input/base_cv.yaml` automáticamente.
  5. Si falla, corregir el YAML y repetir el paso 4 hasta que valide — sin pedirle al usuario que interprete el error.
  6. Recién entonces informar al usuario que su CV base está listo y puede pedir "generá el CV para la oferta [url]".
- Documentar en el skill una tabla de equivalencias orientativa para reducir ambigüedad al mapear secciones reales a tipos genéricos: Experiencia/Educación/Proyectos/Certificaciones/Publicaciones/Voluntariado → `entry_block`; Habilidades/Idiomas/Herramientas/Premios → `simple_list`; Resumen/Perfil/Objetivo → `text_block`.
- `PROMPT_PARA_TU_CV.md` (1.4) sigue existiendo como plan B para quien no tiene Claude Code/Opencode instalado y prefiere pegar el prompt en cualquier chat de IA que ya use.
- Dejar explícito en el skill y en el README que el **layout visual** del CV original (columnas, íconos, foto, colores) no se conserva — solo el contenido. El PDF final sale siempre en el único template Harvard del repo. Esto es intencional (es la garantía de consistencia visual entre usuarios), pero hay que decirlo para que nadie lo espere distinto.

---

## FASE 2 — Generalizar la plantilla de renderizado

### 2.1 Reescribir `templates/cv_template.html`

Reemplazar el bloque `{% if section.kind == 'habilidades' %} ... {% elif 'educacion' %} ... {% elif 'experiencia' %} ... {% elif 'proyectos' %} ... {% endif %}` (sin `else`, hoy pierde contenido en silencio para cualquier otro kind) por:

```jinja
{% if section.type == 'text_block' %}
  <p class="text-block">{{ section.text }}</p>
{% elif section.type == 'simple_list' %}
  <!-- render genérico de items con sus tags opcionales -->
{% elif section.type == 'entry_block' %}
  <!-- render genérico de entries: heading/subheading/location/dates/bullets/links -->
{% else %}
  <!-- fallback visible: renderizar un aviso de tipo no soportado en vez de
       nada, para que el error sea visible durante desarrollo/testing -->
{% endif %}
```

El `templates/cv_style.css` debe cubrir clases genéricas (`.entry-block`, `.simple-list`, `.text-block`) que apliquen el mismo estilo Harvard sin importar el nombre de la sección.

### 2.2 Actualizar `src/render/html_renderer.py`

- `_coerce_sections()` debe operar sobre `type`/`entries`/`items`/`text` (el nuevo schema), no sobre `kind`/`table`.
- `_build_project_links_html()` y `_build_contact_html()` deben generalizarse para trabajar con cualquier `entry_block`/`personal_info.links`, no asumir que solo "proyectos" tiene enlaces.

---

## FASE 3 — Externalizar y generalizar los system prompts

### 3.1 Crear carpeta `prompts/` con los system prompts como archivos de texto plano editables

En vez de mantener `TAILOR_SYSTEM`, `EVALUATOR_SYSTEM`, `REPAIR_SYSTEM` como constantes de string dentro de `src/tailor/prompts.py`, moverlas a archivos de texto plano versionados en el repo:

```
prompts/
├── tailor_system.txt
├── evaluator_system.txt
├── repair_system.txt
└── job_summarizer_system.txt   # nuevo, ver Fase 4.2
```

Objetivo: cualquier usuario —incluso sin experiencia técnica— puede abrir estos archivos y leer exactamente qué se le pide al LLM en cada etapa, y editarlos directamente o pedirle a su IA que los mejore, sin tocar código Python.

### 3.2 Mecanismo de override sin perder personalización en futuras actualizaciones

Crear un loader (en `src/tailor/prompts.py` o un nuevo `src/tailor/prompt_loader.py`):

```python
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

def load_prompt(name: str) -> str:
    """Carga prompts/{name}.override.txt si existe (edición local del
    usuario, gitignored); si no, usa prompts/{name}.txt (default del
    repo, versionado)."""
    override = PROMPTS_DIR / f"{name}.override.txt"
    default = PROMPTS_DIR / f"{name}.txt"
    path = override if override.exists() else default
    return path.read_text(encoding="utf-8").strip()
```

- Agregar `prompts/*.override.txt` a `.gitignore`.
- Así, un `git pull` o una actualización del proyecto nunca pisa la personalización de un usuario que editó su propio prompt — y el usuario no necesita hacer fork ni manejar conflictos de merge para conservar sus cambios.

### 3.3 Contenido de cada prompt (mismas reglas que antes, ahora en texto plano y sin nombres de sección fijos)

Aplicar a `tailor_system.txt`, `evaluator_system.txt` y `repair_system.txt`:

- Eliminar toda referencia literal al enum `educacion|experiencia|proyectos|habilidades`.
- Reescribir las reglas en términos de `type` (`entry_block`/`simple_list`/`text_block`) y del flag `reorderable`:
  - `entry_block` con `reorderable: false` → tratamiento estricto 1:1 (mismo N.º de entradas, mismo orden, bullets editables pero no eliminables).
  - `entry_block` con `reorderable: true` → licencia de reordenar/omitir entradas por relevancia (lo que hoy solo aplica a "proyectos").
  - `simple_list` → reordenable/reformulable (lo que hoy solo aplica a "habilidades").
  - `text_block` → reescribible libremente respetando hechos.
- Eliminar el bloque `SUMMARY FORMAT (HARD RULE)` con la frase literal "En búsqueda de un puesto en...". Esa regla se convierte en contenido del `preferences.txt` de Axel (ver Fase 5), no en una regla genérica del prompt.
- Actualizar el ejemplo de `OUTPUT FORMAT` (el JSON que el prompt le pide al modelo) para reflejar `type`/`entries`/`items`/`text` en vez de `kind`/`entries`/`table`.
- `job_summarizer_system.txt` (nuevo, ver 4.2) debe declarar explícitamente que el texto de la oferta laboral es un dato a resumir, nunca una instrucción a seguir (ver nota de seguridad transversal al final de este documento).

### 3.4 Actualizar `src/tailor/prompts.py`

Las funciones `build_tailor_prompt` / `build_evaluator_prompt` / `build_repair_prompt` ahora:
1. Cargan el system prompt correspondiente vía `load_prompt(...)` (3.2) — texto estático, igual para todas las corridas.
2. Construyen el mensaje "user" dinámicamente (CV base, resumen de la oferta, y opcionalmente el bloque de preferencias — ver Fase 5.3) igual que ya hacían antes.

### 3.5 Reescribir `src/tailor/cv_rewriter.py::_validate_shape()`

- Sustituir el `if kind == "habilidades" / elif "proyectos" / else` por validación genérica según `section.type`, usando `section.reorderable` para decidir si aplica chequeo estricto 1:1 o flexible.
- Eliminar el chequeo determinístico hardcodeado `if base_summary.startswith("En búsqueda de un puesto en")` — ya no corresponde a una regla genérica del pipeline (ver Fase 5 para dónde vive ahora esta preferencia).
- **Nuevo chequeo determinístico, sin LLM:** marcar/descartar cualquier bullet cuyo `text` quede vacío o reducido a solo un separador (`-`, `•`, etc.) tras aplicar `strip()`, y cualquier `entry` cuyo `heading` quede vacío. Esto cubre directamente el riesgo de "bullets con guion y sin texto" en el PDF final sin depender de que el evaluador (LLM) lo detecte — es una validación de forma, resuelta con código, no con inteligencia del modelo (ver discusión en el chat sobre por qué este riesgo específico no necesita LLM para prevenirse).

---

## FASE 4 — Optimización de consumo de tokens

### 4.1 Instrumentar `src/tailor/llm_client.py`

- Capturar `completion.usage` (prompt_tokens / completion_tokens / total_tokens) en cada llamada dentro de `LLMClient.chat()`.
- Loguearlo (nivel INFO o DEBUG) con contexto de qué pase fue (tailor/evaluate/repair/job_summary) y para qué oferta, para tener visibilidad real antes de seguir optimizando.

### 4.2 Crear `src/tailor/job_summarizer.py` (nuevo paso del pipeline)

- Nueva función `summarize_job(client, job: JobInfo) -> JobSummary` con su propio system prompt (`JOB_SUMMARIZER_SYSTEM`), que reduce la descripción cruda de la oferta (300-600 palabras típicas) a un JSON estructurado:
  ```json
  {"requisitos_duros": [...], "skills_deseadas": [...], "funciones_clave": [...]}
  ```
- El prompt debe tratar explícitamente el texto de la oferta como **datos**, nunca como instrucciones (mitigación de prompt injection vía ofertas laborales maliciosas — ver nota de seguridad más abajo).
- Este resumen se calcula **una sola vez por oferta** y se cachea junto al resto de archivos de la carpeta de salida (`job_summary.json`), no se recalcula en cada re-ejecución salvo `--force`.
- Este resumen —no la descripción cruda— es lo que se envía en las 3 pasadas siguientes (tailor, evaluator, repair), vía `JobInfo` o un nuevo campo derivado.

### 4.3 Filtrado local de bullets antes del tailor pass (opcional, mayor impacto)

- Antes de construir el prompt de tailor, filtrar localmente (sin LLM — comparación de `tags` de cada bullet del CV base contra las keywords del `job_summary`, por texto o embeddings simples) el subconjunto de bullets con mejor match, en vez de enviar el banco completo del usuario si este es mucho más grande que lo que terminará usándose.
- Este paso requiere que `base_cv.yaml` tenga bullets etiquetados con `tags` (ya contemplado en el schema de la Fase 1).

### 4.4 Fusionar evaluate + repair condicionalmente (opcional)

- Evaluar si el proveedor LLM soporta pedir, en una sola llamada, que el modelo evalúe y —si encuentra problemas— corrija directamente en el mismo turno, devolviendo `needs_repair: bool` + el JSON corregido cuando aplica. Esto evita reenviar CV completo + oferta una segunda vez cuando sí hace falta reparar.

### 4.5 Prompt caching (opcional, depende del proveedor)

- Si `LLM_BASE_URL` apunta a un proveedor con soporte de context/prompt caching de prefijo, estructurar las llamadas para que el bloque estático (system prompt + schema) vaya siempre igual y primero, y lo variable (CV del usuario, resumen de oferta) al final.

### 4.6 Hacer evaluate/repair configurable, no obligatorio

El riesgo de que se rompa el **formato** visual (negritas/cursivas fuera de lugar, HTML mal armado) ya está resuelto estructuralmente por el diseño del pipeline: el LLM de tailoring nunca genera HTML, solo JSON de texto plano que Jinja2 renderiza con una plantilla fija — el modelo no tiene forma de "escribir mal" una etiqueta que nunca escribe. El chequeo determinístico de bullets vacíos (3.5) cubre el resto de ese riesgo puntual, sin necesidad de LLM.

Evaluate/repair existen para un riesgo distinto: **veracidad del contenido** (alucinaciones, copia literal de la oferta) — un riesgo real incluso con un modelo capaz, porque "tailorizar hacia una oferta" incentiva estructuralmente a embellecer. Para que cada usuario decida su propio trade-off costo/seguridad:

- Agregar `enable_evaluation: bool` a `src/config.py`, leído desde `.env` como `ENABLE_EVALUATION` (default `true`).
- En `run.py`, si `settings.enable_evaluation` es `false`, saltar los pasos de evaluate y repair del pipeline y pasar directo del tailor pass a la reinyección de URLs + render — ahorra las 2 llamadas más caras del pipeline.
- Mantener el default en `true`: el público objetivo incluye personas que no necesariamente van a revisar a mano si el LLM inventó una certificación o exageró una skill en un documento que van a enviar a un empleador real.
- Documentar la variable en `.env.example`, explicando brevemente qué se deja de verificar al desactivarla (no detecta alucinaciones ni copiado literal de la oferta).

---

## FASE 5 — Preferencias personales del usuario (instrucciones opcionales al LLM)

### 5.1 Crear `src/profile/preferences.py`

```python
from pathlib import Path

def load_user_preferences(path: Path) -> str:
    """Lee un archivo de texto plano. Líneas vacías o que empiecen con '#'
    se ignoran. Devuelve '' si el archivo no existe o queda vacío tras
    filtrar comentarios."""
    if not path.exists():
        return ""
    lines = [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(lines).strip()
```

### 5.2 Agregar `preferences_path` a `src/config.py`

```python
preferences_path: Path
...
preferences_path=_env_path("PREFERENCES_PATH", "input/preferences.txt"),
```

Y la variable correspondiente documentada en `.env.example`.

### 5.3 Inyectar en el mensaje dinámico ("user"), nunca en los archivos `.txt` del system prompt

Importante dado el cambio de la Fase 3: el bloque de preferencias **no** se mezcla dentro de `prompts/*.txt` (esos archivos deben quedar estáticos y 100% editables/legibles como las reglas generales del pipeline, iguales para todos los usuarios salvo que alguien cree su propio `.override.txt`). Las preferencias son dato dinámico por usuario, así que se agregan en la construcción del mensaje "user" que ya arman `build_tailor_prompt`, `build_evaluator_prompt` y `build_repair_prompt` (Fase 3.4), igual que ya hacen con el CV base y el resumen de la oferta:

- Las tres funciones aceptan un nuevo parámetro `user_preferences: str = ""`.
- Si no está vacío, se agrega al mensaje "user" un bloque delimitado y explícitamente subordinado a las reglas críticas del system prompt:

```
=== INSTRUCCIONES PERSONALES DEL CANDIDATO ===
El candidato dejó estas preferencias adicionales de estilo/formato.
Síguelas al pie de la letra SIEMPRE QUE no contradigan las reglas
críticas del system prompt (no inventar datos, no copiar literal de la
oferta, no cambiar hechos). Si hay conflicto, las reglas críticas ganan.

{user_preferences}
=== FIN INSTRUCCIONES PERSONALES ===
```

- Si `user_preferences` está vacío, no se agrega ningún bloque (comportamiento neutral por defecto).

### 5.4 Nuevo tipo de issue en `EVALUATOR_SYSTEM`

```
12. "custom_instruction_violated": el candidato dejó una instrucción
    personal explícita (ver bloque de INSTRUCCIONES PERSONALES) y el CV
    adaptado la ignoró o la contradijo. Severidad "medium" por defecto,
    "high" si la instrucción pedía un formato exacto y no se siguió.
```

### 5.5 Wiring en `run.py`

Cargar una vez por corrida y pasarlo a las tres llamadas:

```python
from src.profile.preferences import load_user_preferences
...
user_prefs = load_user_preferences(settings.preferences_path)
...
tailored = tailor_cv(client, base_profile, job_info, user_preferences=user_prefs)
evaluation = evaluate(client, base_profile, job_info, tailored.tailored_json, user_preferences=user_prefs)
repaired = repair_cv(client, base_profile, tailored.tailored_json, semantic_issues, user_preferences=user_prefs)
```

### 5.6 Archivos de preferencias

- `input/preferences.txt` — gitignored (dato personal de cada usuario), igual tratamiento que `base_cv.yaml`.
- `input/preferences.example.txt` — **trackeado en git**, con todo el contenido comentado (`#`), documentando el formato y con un ejemplo comentado. Sirve de plantilla de referencia.
- El contenido real de Axel (la regla del resumen "En búsqueda de un puesto en...") va en su `input/preferences.txt` local, **no** en el repo público ni en ningún prompt genérico.

---

## FASE 6 — Limpieza previa a publicar el repo

### 6.1 Reemplazar/limpiar `scripts/build_base_cv.py`

- El script actual genera un `.docx` (formato que el pipeline ya no consume — desactualizado respecto al `base_cv.html`/`base_cv.yaml` actual) con datos personales reales de Axel hardcodeados (nombre completo, teléfono, correo).
- Reemplazarlo por un generador de `input/base_cv.yaml` de ejemplo/plantilla **vacía o con placeholders genéricos** ("Nombre Apellido", "email@ejemplo.com", "+51 900 000 000"), consistente con el schema de la Fase 1. Alternativamente, eliminarlo si `schema/example.yaml` (Fase 1.2) ya cumple ese rol y resulta redundante.

### 6.2 Auditoría de datos personales

Antes de publicar, correr sobre todo el repo (incluyendo mensajes de commits si es posible):

```bash
grep -ri "axel\|ccasani\|aaronaxel810" -r .
```

Confirmar que no queda ningún dato personal real fuera de `input/` (ya gitignored).

### 6.3 Actualizar `README.md` / `README.en.md`

- Documentar el nuevo formato `input/base_cv.yaml` (reemplazando las referencias a `base_cv.html`).
- Enlazar `schema/base_cv.schema.json`, `schema/example.yaml` y `PROMPT_PARA_TU_CV.md`.
- Documentar cómo generar `base_cv.yaml` a partir de un CV en PDF/Word existente, con dos caminos: (a) pedírselo al agente de código ya instalado (ver 1.7) en una sola instrucción en lenguaje natural, o (b) usar `PROMPT_PARA_TU_CV.md` en cualquier chat de IA. Aclarar explícitamente que el layout visual original no se conserva — el output final siempre usa el template Harvard del repo.
- Documentar `input/preferences.txt` (opcional) con el ejemplo genérico (sin la preferencia personal de Axel).
- Documentar `scripts/validate_base_cv.py` como paso recomendado antes de correr el pipeline completo.
- Documentar la carpeta `prompts/` (los 4 archivos `.txt`), explicando que son legibles y editables directamente por el usuario, y el mecanismo de `*.override.txt` para personalizar sin perder los cambios en futuras actualizaciones del proyecto.
- Actualizar la tabla de variables de `.env` con `PREFERENCES_PATH`, `ENABLE_EVALUATION` y el nuevo default de `BASE_CV_PATH`.

### 6.4 Actualizar `AGENTS.md` / `OPENCODE_INSTRUCTIONS.md` / `.claude/skills/` / `.opencode/command/`

- Revisar que ninguno de estos archivos (usados como contexto para agentes de IA) siga describiendo el formato HTML antiguo, el enum de 4 secciones fijas, ni referencie a Axel como si fuera el único usuario previsto del proyecto.
- Agregar explícitamente la tarea de conversión descrita en 1.7 (*"convertime mi CV a base_cv.yaml"* / *"generá mi CV base a partir de este PDF"*) como una capacidad reconocida y documentada del skill, con su procedimiento paso a paso (leer schema+ejemplo → mapear secciones → escribir YAML → validar con `scripts/validate_base_cv.py` → autocorregir en loop si falla → recién avisar al usuario). Esta es la pieza que permite que alguien sin experiencia técnica use el proyecto de punta a punta sin escribir YAML a mano.

---

## FASE 7 — Verificación

Antes de dar por cerrado el rediseño, correr (o crear si no existen) tests que cubran:

1. `read_cv()` sobre un YAML con secciones de los 3 `type`, incluyendo al menos una sección con nombre no convencional (ej. "Certificaciones") — debe parsear correctamente, no descartarla.
2. `read_cv()` sobre un YAML con un `type` inválido o que no valida contra el schema — debe lanzar una excepción clara, no fallar en silencio.
3. `_validate_shape()` con una sección `reorderable: true` a la que el tailor le quitó una entrada — no debe generar warning. Con una sección `reorderable: false` en la misma situación — sí debe generar warning.
4. Render de `cv_template.html` con las 3 `type` — debe producir HTML visualmente consistente con el estilo Harvard existente, para cualquier combinación/orden de secciones.
5. `load_user_preferences()` — archivo ausente, archivo vacío, archivo solo con comentarios, archivo con contenido real → los 4 casos devuelven el resultado esperado.
6. Verificar que el bloque de instrucciones personales NO aparece en el prompt cuando `preferences.txt` está vacío o ausente.
7. Revisar `tests/test_cv_reader.py`, `tests/test_html_renderer.py`, `tests/test_tailor_pipeline.py` existentes — es probable que muchos asuman el formato HTML/kind actual y necesiten reescribirse para el nuevo schema.
8. `load_prompt()` (3.2) usa `{name}.override.txt` cuando existe y cae a `{name}.txt` cuando no — verificar para los 4 prompts.
9. Con `ENABLE_EVALUATION=false` en `.env`, `python run.py all` completa sin invocar evaluate/repair y aun así produce un `cv.pdf` válido.
10. El chequeo determinístico de bullets vacíos (3.5) descarta/marca correctamente una entrada de prueba con un bullet `"text": "-"` o `"text": ""`, sin intervención del LLM.
11. La tarea de conversión PDF/Word → YAML (1.7): con un CV de prueba en PDF con estructura distinta a la de Axel (ej. con secciones "Certificaciones" e "Idiomas" separadas), el agente produce un `base_cv.yaml` que valida con `scripts/validate_base_cv.py` sin intervención manual del usuario.

---

## Nota de seguridad transversal (aplica a Fase 4.2 y a los prompts en general)

Dado que el proyecto se hará público y correrá contra ofertas laborales publicadas por terceros (no controladas por el mantenedor ni por cada usuario), todo texto proveniente de una oferta de LinkedIn debe tratarse explícitamente como **datos no confiables**, nunca como instrucciones, en cualquier prompt donde se incluya. Esto cierra la superficie de un posible prompt injection embebido en una descripción de oferta maliciosa. El `JOB_SUMMARIZER_SYSTEM` (Fase 4.2) es el lugar natural para dejar esto explícito primero, ya que es el único paso que procesa el texto crudo de la oferta.
