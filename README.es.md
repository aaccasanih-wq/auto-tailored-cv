# auto-tailored-cv

> Adapta automáticamente tu CV para cada oferta laboral que guardaste en LinkedIn — entrega `.html` + `.pdf` — sin que parezca un relleno de keywords obvio.

`Documentación en español | [Read in English](README.md)`

---

## ⚠️ Aviso legal

Esta herramienta automatiza interacciones con LinkedIn usando tu propia sesión de navegador iniciada, vía [Playwright MCP](https://github.com/microsoft/playwright-mcp). **Los Términos de Servicio de LinkedIn prohíben el acceso automatizado** a la plataforma. Usar esta herramienta puede derivar en restricciones temporales o permanentes de tu cuenta de LinkedIn. Úsala bajo tu propio riesgo. Los autores de este proyecto no se hacen responsables de ninguna consecuencia, incluida la suspensión de tu cuenta.

El proyecto **no almacena, transmite ni vende** tus credenciales de LinkedIn. Todo el scraping ocurre localmente en tu máquina usando un perfil persistente de Chromium.

---

## Qué hace

Dada una carpeta con tu CV base en formato HTML (texto plano + hipervínculos, sin imágenes), el sistema:

1. **Extrae** — se conecta a tu sesión de LinkedIn vía Playwright MCP (con `--user-data-dir` persistente para no tener que reloguearte), navega a la página de "Empleos guardados" y obtiene de cada oferta guardada: título, empresa, ubicación, requisitos y descripción completa. El auto-wait nativo (`browser_wait_for`) resuelve el problema histórico de páginas de LinkedIn que no terminaban de cargar, y el scraper ahora hace clic en el botón "...más" / "See more" que LinkedIn muestra en descripciones largas para capturar el texto completo (no solo los primeros ~400 caracteres).
2. **Perfila** — lee tu CV base `.html` con BeautifulSoup4 y lo estructura en secciones tipadas (`educación`, `experiencia`, `proyectos`, `habilidades`). Los hipervínculos se extraen como objetos protegidos `{texto, url}` — las URLs **nunca** llegan al LLM.
3. **Adapta** — llama a un LLM (vía cualquier endpoint OpenAI-compatible; por defecto suscripción OpenCode Go + DeepSeek V4 Flash) para reescribir el CV alineándolo de forma natural con los requisitos de la oferta. El prompt prohibe explícitamente:
   - inventar skills o experiencias que no tienes,
   - copiar frases literales de la oferta,
   - hacer keyword stuffing,
   - modificar fechas o roles,
   - inventar o modificar URLs (los hipervínculos están protegidos de extremo a extremo),
   - dejar paréntesis vacíos "()" en el `descriptor` de un proyecto.
   El prompt **permite**: reordenar proyectos por relevancia, eliminar un proyecto completo si no aporta a esa postulación, editar el `descriptor` parentético de un proyecto (p.ej. "(Agentic AI · RAG · Automatización)" → "(IA · Automatización · RAG)" o "" cuando no aporte), y dividir un bullet largo en varios más cortos (o mergear dos en uno) si el resultado es más claro. El primer bullet de cada proyecto debe describir **qué es el proyecto / qué hace / qué problema resuelve**; los siguientes bulets son detalles de implementación.
4. **Evalúa** — un segundo pase del LLM revisa el CV adaptado contra la oferta y tu CV base, marcando alucinaciones, incongruencias, problemas de formato y cualquier alineación "forzada". El evaluador conoce las reglas específicas por sección, así que no marca como error de formato el reordenamiento / eliminación de proyectos o la edición del `descriptor` cuando son legítimos.
5. **Repara** — si el evaluador encontró problemas, un tercer pase corrige solo los issues marcados.
6. **Renderiza HTML** — Jinja2 renderiza `cv.html` a partir de `templates/cv_template.html`, reutilizando `templates/cv_style.css`. El encabezado (nombre, contacto, tagline) está **centrado** arriba. El párrafo de descriptor del proyecto fusiona el `descriptor` (posiblemente editado) con los `enlaces` protegidos: links cuyo texto aparece en el descriptor se vuelven `<a href>` inline; los links restantes se agregan tras un separador " · ". Descriptores vacíos no producen paréntesis. Cada bloque reescribible lleva `contenteditable="true"` + un `data-field` único para edición in-place.
7. **Renderiza PDF** — Playwright Chromium headless convierte `cv.html` → `cv.pdf` vía `page.pdf(format="Letter", print_background=True)`.
8. **Revisión opcional** — `python run.py review <job_slug>` sirve `cv.html` localmente; las ediciones se envían a `/save` y regeneran `cv.pdf` en vivo.

Para cada oferta guardada se genera una carpeta:

```
output/2026-07-13_senior-data-engineer_acme/
├── cv.html                        # render Jinja editable
├── cv.pdf                         # PDF generado por Playwright
├── cv_style.css                   # copiado junto a cv.html (autocontenible)
├── job_description.txt            # extracción cruda
├── analysis.json                  # CV adaptado (estructurado, urls protegidas)
└── evaluation.json                # veredicto del evaluador + reparaciones
```

El pipeline es **incremental**: al re-ejecutar solo procesa las ofertas nuevas. Las ya procesadas se saltan a menos que pases `--force`.

---

## Arquitectura

```
extract  →  profile  →  tailor  →  evaluate  →  repair  →  render_html  →  [review opcional]  →  render_pdf
(Playwright MCP)  (bs4)        (OpenCode Go / GLM 5.2 o cualquier endpoint OpenAI-compatible)  (Jinja2)         (Playwright Chromium)
```

Tres pases de LLM — *tailor* (reescribe), *evaluate* (revisa), y *repair* (corrige) — usan el mismo modelo configurable. Ver [`docs/PROMPT_DESIGN.md`](docs/PROMPT_DESIGN.md) para la estrategia anti-sospecha del prompt y el diseño de protección de URLs.

### Protección de URLs / hipervínculos

Cada `<a href>` del CV base se extrae como un objeto estructurado `{texto, url}`. Los prompts de tailor / evaluator / repair **omiten las URLs por completo** — el LLM solo ve el texto visible. Después del tailor, los arrays `enlaces` originales se reinsertan byte-identical, así los links del CV sobreviven intactos y un LLM con bugs no puede modificarlos.

---

## Estructura del proyecto

```
auto-tailored-cv/
├── .github/workflows/ci.yml
├── .gitignore
├── .env.example                # plantilla de secrets (se commitea)
├── .env                        # tus secrets reales (NUNCA se commitea)
├── .claude/skills/cv_automatizacion.md    # skill de lenguaje natural
├── .opencode/command/cv_automatizacion.md # equivalente para opencode
├── README.md                   # este archivo (inglés)
├── README.es.md                # versión en español
├── LICENSE                     # MIT
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt        # + pytest, ruff, python-docx (legacy --legacy-docx)
├── run.py                      # entrypoint CLI
├── input/
│   └── base_cv.html            # tu CV base (texto + hipervínculos, gitignored)
├── jobs/                       # JSONs extraídos (gitignored, caché)
├── output/                     # CVs generados (gitignored)
├── templates/
│   ├── cv_template.html        # plantilla Jinja2 para el CV adaptado
│   └── cv_style.css            # estilos compartidos (header, secciones, skills-table, @media print)
├── src/
│   ├── config.py               # Settings (LLM_*, scraper, paths, review server)
│   ├── extract/
│   │   ├── linkedin_scraper.py # Playwright MCP (default) / Browser MCP (legacy)
│   │   └── mcp_stdio.py        # cliente JSON-RPC 2.0 genérico sobre stdio
│   ├── profile/
│   │   └── cv_reader.py        # Parser HTML (BeautifulSoup4) → CVProfile
│   ├── tailor/
│   │   ├── llm_client.py       # Wrapper del SDK de OpenAI (cualquier endpoint OpenAI-compatible)
│   │   ├── prompts.py          # Builders de prompts (tailor / evaluator / repair)
│   │   ├── cv_rewriter.py      # Pase tailor + re-inyección de enlaces
│   │   ├── evaluator.py
│   │   └── repair.py
│   ├── render/
│   │   ├── html_renderer.py    # Jinja2 → cv.html
│   │   ├── pdf_renderer.py     # Playwright → cv.pdf
│   │   └── legacy/             # solo --legacy-docx
│   │       ├── docx_writer.py
│   │       └── pdf_converter.py
│   ├── review/
│   │   └── server.py           # servidor local de revisión (FastAPI)
│   └── utils/
│       ├── slugify.py
│       └── logging.py
└── tests/
    ├── test_cv_reader.py
    ├── test_html_renderer.py
    ├── test_pdf_renderer.py
    ├── test_tailor_pipeline.py
    ├── test_run_cli.py
    ├── test_linkedin_scraper.py
    ├── test_mcp_stdio.py
    ├── test_slugify.py
    ├── test_helpers_llm.py
    └── legacy/                 # tests del modo --legacy-docx
        ├── test_docx_writer.py
        └── test_pdf_converter.py
```

---

## Instalación

### Requisitos

- macOS (probado en macOS 14+; Linux debería funcionar con ajustes menores)
- Python 3.9+
- Un proveedor de LLM con endpoint OpenAI-compatible (por defecto OpenCode Go suscripción en `https://opencode.ai/zen/go/v1` con DeepSeek V4 Flash como modelo; obtén tu API key en <https://opencode.ai/auth>). Cualquier alternativa — GLM 5.2 en el mismo tier Go, OpenRouter, DeepSeek directo, … — funciona cambiando `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_TAILOR`, `LLM_MODEL_EVALUATOR` en `.env`.
- Playwright Chromium — se instala con:
  ```bash
  python3 -m playwright install chromium
  ```
- Playwright MCP (para ejecutar scraping) — se lanza automáticamente. Te logueas en LinkedIn una vez en la primera corrida y la sesión persiste en `.playwright-profile/`.
- LibreOffice es **opcional** (solo si usas `--legacy-docx`); el pipeline principal usa Playwright para generar PDFs.

### Setup

```bash
git clone https://github.com/<tu-usuario>/auto-tailored-cv.git
cd auto-tailored-cv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # tests + path legacy docx
python3 -m playwright install chromium

cp .env.example .env
# abre .env y pega tu API key del proveedor LLM (LLM_API_KEY)

# coloca tu CV base aquí (HTML plano; input/base_cv.html es un ejemplo)
cp /ruta/a/tu/base_cv.html input/base_cv.html
```

Para usar el fallback `--scraper browsermcp` en vez de Playwright MCP, instala la [extensión de Browser MCP para Chrome](https://docs.browsermcp.io) (mantenida solo durante la transición).

---

## Uso

```bash
# Pipeline completo (extrae todos los empleos guardados, adapta cada uno, renderiza HTML + PDF)
python run.py all

# Solo scrapear LinkedIn → jobs/*.json
python run.py extract

# Solo adaptar los empleos ya extraídos
python run.py tailor

# Solo procesar empleos guardados desde la última corrida (incremental)
python run.py all --new

# Re-procesar una URL específica, ignorando caché
python run.py all --job https://www.linkedin.com/jobs/view/<id> --force

# Dry run: muestra qué se procesaría sin llamar al LLM
python run.py all --dry-run

# Cambiar temporalmente el backend de scraping
python run.py all --scraper browsermcp     # legacy fallback
python run.py all --scraper playwright     # default

# Usar el render legacy docx + LibreOffice (en vez de HTML + Playwright)
python run.py all --legacy-docx

# Editar un CV adaptado en el navegador + regenerar PDF
python run.py review 2026-07-13_senior-data-engineer_acme
```

Los resultados van a `output/<fecha>_<slug>_<empresa>/`. Al terminar, `python run.py all` imprime la ruta de cada `cv.pdf` generado + un recordatorio de que `review <slug>` está disponible si algún resultado no te convenció.

### Ejecutarlo desde un asistente desktop (Claude Desktop / Opencode Desktop / Kimi Desktop)

Si preferís no usar la terminal, podés pedirle a un asistente desktop que
soporte skills / comandos custom que corra el pipeline por vos. El repo
trae dos copias del skill:

- `.claude/skills/cv_automatizacion.md` — auto-cargado por **Claude Desktop**
  y **Claude Code** cuando los abrís en la raíz del proyecto. Decí en
  lenguaje natural: *"Generá el CV para la oferta
  https://www.linkedin.com/jobs/view/123/"* y Claude corre
  `python run.py all --job <url> --force` por vos.
- `.opencode/command/cv_automatizacion.md` — mismo contenido, expuesto como
  el comando `/cv_automatizacion` en **Opencode Desktop / CLI**.

Para **Kimi Desktop** (sin auto-cargador de skills), pegá manualmente el
contenido de `.claude/skills/cv_automatizacion.md` en tus custom
instructions o system prompt.

El skill le enseña al asistente a traducir pedidos en lenguaje natural
("solo las nuevas", "regenerar este: <url>", "revisar el de <job_slug>"…)
a los flags correctos del CLI. El output y el comportamiento son idénticos
a correr el CLI a mano.

---

## Configuración (`.env`)

| Variable | Default | Descripción |
|---|---|---|
| `LLM_API_KEY` | — | API key del proveedor LLM (obligatoria). Acepta cualquier endpoint OpenAI-compatible |
| `LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` | Endpoint OpenAI-compatible (OpenCode Go, DeepSeek, OpenRouter, …) |
| `LLM_MODEL_TAILOR` | `deepseek-v4-flash` | Modelo para el pase de reescritura |
| `LLM_MODEL_EVALUATOR` | `deepseek-v4-flash` | Modelo para los pases de evaluación + reparación |
| `LLM_REQUEST_TIMEOUT` | `120` | Timeout por request (segundos) |
| `SCRAPER_BACKEND` | `playwright` | `playwright` (default) o `browsermcp` (legacy) |
| `PLAYWRIGHT_MCP_COMMAND` | `npx` | Comando NPM que lanza Playwright MCP |
| `PLAYWRIGHT_MCP_ARGS` | `-y @playwright/mcp@latest` | Args para Playwright MCP |
| `PLAYWRIGHT_USER_DATA_DIR` | `.playwright-profile` | Perfil persistente de Chromium (cookies de LinkedIn). Gitignored — contiene secrets |
| `BROWSER_MCP_COMMAND` | `npx` | (legacy) Comando NPM que lanza Browser MCP |
| `BROWSER_MCP_ARGS` | `-y @browsermcp/mcp@latest` | (legacy) Args para Browser MCP |
| `LINKEDIN_SAVED_JOBS_URL` | `https://www.linkedin.com/my-items/saved-jobs/` | URL de empleados guardados |
| `BROWSER_TIMEOUT_MS` | `15000` | Timeout por acción (ms) |
| `BROWSER_NAV_DELAY_S` | `3` | Sleep fallback antes del snapshot. Con el auto-wait de Playwright MCP es prácticamente redundante |
| `BASE_CV_PATH` | `input/base_cv.html` | Ruta a tu CV base |
| `JOBS_DIR` | `jobs` | Directorio de caché de empleos |
| `OUTPUT_DIR` | `output` | Dónde se escriben los CVs adaptados |
| `TEMPLATES_DIR` | `templates` | Plantillas Jinja + CSS compartido |
| `SOFFICE_PATH` | `soffice` | Binario de LibreOffice (solo usado por `--legacy-docx`) |
| `REVIEW_HOST` | `localhost` | host para `run.py review` |
| `REVIEW_PORT` | `8420` | puerto para `run.py review` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Copia `.env.example` a `.env` y completa tus valores. Los defaults apuntan a OpenCode Go (suscripción) + DeepSeek V4 Flash; para cambiar de proveedor solo modifica `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_TAILOR` y `LLM_MODEL_EVALUATOR`.

---

## Seguridad / privacidad

- Tu `.env`, `input/base_cv.html` y `.playwright-profile/` están gitignored — ni tu CV ni tus cookies salen de tu máquina vía git.
- El scraping de LinkedIn usa tu sesión real de Chromium vía Playwright MCP — ninguna contraseña se almacena en este proyecto.
- Las URLs del CV base están protegidas de extremo a extremo: no aparecen en los prompts del LLM y se reinsertan byte-identical después del pase tailor.
- Todos los prompts al LLM van a tu endpoint configurado con la política de retención de datos que tenga ese proveedor (OpenCode Go por defecto tiene cero retención).

---

## Contribuciones

Es un proyecto personal pero las PRs son bienvenidas — abre primero un issue para discutir qué quieres cambiar.

---

## Licencia

MIT — ver [`LICENSE`](LICENSE).