# Instrucciones para opencode — migración a HTML/CSS + review + Playwright MCP

Contexto: este es el proyecto `auto-tailored-cv`, cuyo estado actual está
descrito en `AGENTS.md` y `README.md` en la raíz del repo. Ya coloqué los
siguientes archivos nuevos en el repo antes de pedirte esto:

- `input/base_cv.html` — migración del CV base (antes `input/base_cv.docx`),
  sin foto ni imágenes — es texto e hipervínculos únicamente.
- `templates/cv_style.css` — CSS compartido entre el CV base y las CVs
  generadas por el pipeline

Quiero que modifiques el resto del proyecto para que todo el pipeline sea
consistente con estos archivos. A continuación el detalle exacto de cambios.

No toques: `src/tailor/` (llm_client, prompts, cv_rewriter, evaluator,
repair), el esquema general de `jobs/`, ni la lógica de caching/incremental
de `run.py`.

---

## 1. Nueva fuente de verdad del CV base

- `BASE_CV_PATH` en `.env` / `.env.example` pasa de `input/base_cv.docx` a
  `input/base_cv.html`.
- Reescribe `src/profile/cv_reader.py` para que parsee HTML (usa
  `beautifulsoup4`, agrégalo a `requirements.txt`) en vez de `python-docx`.
  El `CVProfile` resultante debe distinguir explícitamente:
  - texto reescribible (bullets de experiencia, resumen, descripciones de
    proyecto)
  - campos NO reescribibles: nombre, contacto, fechas, y sobre todo los
    **hipervínculos** — cada `<a href="...">texto</a>` debe extraerse como
    un objeto `{"texto": "...", "url": "..."}` separado, nunca como string
    plano concatenado con la URL.
- `input/base_cv.docx` y `input/base_cv_original_backup.docx` quedan como
  archivos históricos, ya no se leen en el pipeline. `input/base_cv.md`
  puede eliminarse (era un intento anterior con Markdown, ya no aplica).

## 2. El esquema de `analysis.json` debe preservar los links

En `src/tailor/cv_rewriter.py` y en `_validate_shape`, cualquier proyecto o
sección que tenga enlaces debe mantener el array `enlaces` (o el nombre que
uses) intacto entre el CV base y el CV tailorizado — el LLM solo debe recibir
y devolver los campos de texto reescribible (bullets, resumen), nunca los
`url`. Ajusta `src/tailor/prompts.py` para instruir explícitamente esto:
"no modifiques ni inventes URLs; los campos `url` no forman parte de tu
tarea de reescritura".

## 3. Reemplazar el renderizado docx → pdf por HTML → PDF

Eliminar (o mover a `src/render/legacy/` si quieres conservarlos detrás de
un flag `--legacy-docx`, opcional):
- `src/render/docx_writer.py`
- `src/render/pdf_converter.py` (la parte que invoca `soffice`)

Agregar:
- `src/render/html_renderer.py`: usa **Jinja2** (agrégalo a
  `requirements.txt`) para renderizar una plantilla
  `templates/cv_template.html` (créala tú, basándote en la estructura de
  `input/base_cv.html` que ya coloqué, y reutilizando `templates/cv_style.css`)
  con los datos de `analysis.json` de cada job. Cada bloque de texto
  reescribible debe llevar `contenteditable="true"` y `data-field="..."`.
  Salida: `output/<job_slug>/cv.html`.
- `src/render/pdf_renderer.py`: usa **Playwright** (agrégalo a
  `requirements.txt`; el setup debe correr `playwright install chromium`)
  para abrir `cv.html` en Chromium headless y generar
  `output/<job_slug>/cv.pdf` vía `page.pdf(format="Letter", print_background=True)`.
- Control de saltos de página: en `templates/cv_style.css`, la regla
  `break-inside: avoid` debe aplicarse a nivel de **cada entrada individual**
  (`.entry-block`, `.project-block`), nunca a la `.section` completa. Ya
  corregí esto en `templates/cv_style.css` (el archivo que coloqué en el
  repo) — mantenlo así al generar `cv_template.html`, reutilizando las
  mismas clases (`entry-block`, `project-block`, `section-title` con
  `break-after: avoid`). Aplicar el avoid a nivel de sección completa deja
  espacio en blanco al final de la página anterior cuando la sección no
  cabe entera; a nivel de entrada evita eso y solo previene que un bullet
  se corte a la mitad.

## 4. Paso de revisión editable (`run.py review <job_slug>`)

Agregar `src/review/server.py`:
- Servidor local mínimo (usa `fastapi` + `uvicorn`, agrégalos a
  `requirements.txt`, o `http.server` si prefieres cero dependencias nuevas).
- `GET /` sirve `output/<job_slug>/cv.html`.
- `POST /save` recibe el HTML editado completo (`outerHTML`), sobreescribe
  `output/<job_slug>/cv.html`, y llama a `pdf_renderer.render()` sobre ese
  mismo archivo para regenerar `cv.pdf`.
- En la plantilla, agrega un botón fijo (position: fixed, esquina) del
  estilo `<button onclick="guardarYGenerarPDF()">Guardar y generar PDF</button>`
  con el JS correspondiente (`fetch('/save', {method:'POST', body: ...})`).
  Este botón NO debe aparecer en el PDF final (usa `@media print { display:none }`
  en `cv_style.css`).

En `run.py`, agrega el subcomando:
```
python run.py review <job_slug>
```
que levanta el servidor en `localhost` (puerto configurable via `.env`,
default 8420) y abre el navegador con `webbrowser.open()`.

El flujo principal (`run.py all` / `run.py tailor`) NO debe pausarse a
esperar aprobación — genera html + pdf automáticamente para todos los jobs,
y al final imprime en consola la ruta de cada pdf generado más un recordatorio
de que `review <slug>` está disponible si algo no convenció.

## 5. Migrar Browser MCP → Playwright MCP

- Modifica `src/extract/linkedin_scraper.py` para hablar con **Playwright
  MCP** (`@playwright/mcp`) en vez de Browser MCP. Usa un perfil de usuario
  persistente (flag `--user-data-dir`, configurable via nueva variable
  `PLAYWRIGHT_USER_DATA_DIR` en `.env.example`, default `.playwright-profile`)
  para conservar la sesión logueada de LinkedIn sin relogueo manual.
- `src/extract/mcp_stdio.py` se mantiene igual (cliente JSON-RPC genérico
  sobre stdio); solo cambian qué servidor se lanza y qué tools se invocan
  (`browser_navigate`, `browser_snapshot`, `browser_wait_for`, etc.).
- Aprovecha el auto-wait nativo de Playwright MCP (`browser_wait_for`) para
  resolver el problema histórico de páginas de LinkedIn que no terminaban de
  cargar antes del scraping — ese es el motivo principal de este cambio.
- Deja Browser MCP como fallback opcional detrás de una flag
  `--scraper browsermcp|playwright` (default `playwright`) durante la
  transición, en vez de borrarlo de inmediato.
- Actualiza `AGENTS.md`: reemplaza la regla "no add linkedin-mcp-server...
  we talk to Browser MCP directly" por la referencia a Playwright MCP y el
  motivo (auto-wait).

## 6. Skill / comando de invocación en lenguaje natural

Crea `.claude/skills/cv_automatizacion.md` (y su equivalente para el
directorio de comandos custom de opencode, si existe uno distinto) con:

```markdown
---
name: cv_automatizacion
description: Genera y/o revisa CVs tailorizados para las ofertas guardadas en LinkedIn
---

Cuando el usuario pida generar, tailorizar, actualizar o revisar sus CVs
para ofertas laborales guardadas en LinkedIn, ejecuta en bash desde la
raíz del proyecto auto-tailored-cv:

    python run.py all [--new] [--job <url>] [--force]

Interpreta el pedido del usuario para armar los flags:
- "solo las nuevas" / "las que faltan" → --new
- "esta oferta en particular: <url>" → --job <url>
- "vuelve a generar aunque ya exista" → --force
- "no llames al LLM, solo revisa qué haría" → --dry-run

Si el usuario quiere revisar/editar un CV antes del PDF final, o dice que
no le convenció el resultado para un puesto específico:

    python run.py review <job_slug>

Muestra siempre al usuario la ruta final de los archivos generados
(cv.html, cv.pdf) al terminar.
```

No crear un subagente de IA nuevo ni un servidor MCP propio para esto —
la orquestación ya es determinística en `run.py`, envolverla en otro agente
solo añade latencia sin resolver ninguna ambigüedad real.

## 7. Tests

- Agregar `tests/test_html_renderer.py`: valida que `html_renderer.render()`
  produce HTML válido con todos los `data-field` esperados y que los
  hipervínculos del `analysis.json` aparecen intactos como `<a href>`.
- Agregar `tests/test_pdf_renderer.py`: valida que `pdf_renderer.render()`
  produce un PDF no vacío (usa un HTML de fixture simple; marca como test
  lento/opcional en CI si Playwright no está disponible en ese entorno).
- Eliminar o mover a `tests/legacy/`: `tests/test_docx_writer.py`,
  `tests/test_pdf_converter.py` (si existen con esos nombres).
- Actualizar `tests/test_tailor_pipeline.py` si el shape de `analysis.json`
  cambia por los nuevos campos `enlaces`/`url`.
- Confirmar `pytest tests/ -v` 100% verde al terminar, como exige `AGENTS.md`.

## 8. Documentación

- Actualiza `README.md` y `README.es.md`:
  - Reemplaza la sección de render (python-docx + LibreOffice) por
    Jinja2 + Playwright.
  - Agrega el paso `review` opcional al flujo.
  - Actualiza el diagrama de pipeline:
    `extract → profile → tailor → evaluate → repair → render_html → [review opcional] → render_pdf`
  - Actualiza el árbol de `output/<job_slug>/` para incluir `cv.html` junto
    a `cv.pdf`.
  - Actualiza prerequisitos: agrega `playwright install chromium`, aclara
    que LibreOffice pasa a ser opcional (solo modo `--legacy-docx`), agrega
    Playwright MCP a los requisitos de scraping.
- Actualiza `AGENTS.md`:
  - "Architecture map": agrega `html_renderer.py`, `pdf_renderer.py`,
    `review/server.py`, `cv_style.css`, `cv_template.html`.
  - Reglas de Browser MCP → Playwright MCP (punto 5).
  - Nueva regla: "los `url` de hipervínculos nunca pasan por el LLM; son
    campos protegidos en el schema de `analysis.json`".

## 9. Generalizar las variables de entorno del proveedor LLM

Actualmente `.env` / `.env.example` y `src/config.py` usan nombres atados a
un proveedor específico (`OPENCODE_API_KEY`, `OPENCODE_BASE_URL`,
`OPENCODE_MODEL_TAILOR`, `OPENCODE_MODEL_EVALUATOR`). Como `llm_client.py`
ya usa el SDK oficial de `openai` apuntado a una URL base configurable
(cualquier endpoint compatible con la API de OpenAI sirve — OpenCode Go,
DeepSeek, etc.), renómbralas a algo neutral respecto al proveedor:

```
LLM_API_KEY=...
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL_TAILOR=glm-5.2
LLM_MODEL_EVALUATOR=glm-5.2
```

- Actualiza `src/config.py` (`Settings`) y todos los usos de las variables
  viejas en `src/tailor/llm_client.py` y donde más aparezcan.
- Actualiza `.env.example` con los nuevos nombres y un comentario aclarando
  que `LLM_BASE_URL` acepta cualquier endpoint compatible con la API de
  OpenAI (ejemplos en comentario: OpenCode Go, DeepSeek, OpenRouter, etc.).
- Actualiza la tabla de configuración en `README.md` / `README.es.md` con
  los nuevos nombres.
- No cambies el comportamiento por defecto: los valores default siguen
  siendo los de OpenCode Go / GLM 5.2, solo cambian los nombres de variable.

---

Al terminar, corre `pytest tests/ -v` y `python run.py --help` para
confirmar que nada quedó roto, y dame un resumen de qué archivos creaste,
modificaste o eliminaste.
