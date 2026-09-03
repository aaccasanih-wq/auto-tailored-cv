# auto-tailored-cv

> Adapta automáticamente tu CV para cada oferta laboral de LinkedIn — entrega `.html` + `.pdf` — sin que parezca un relleno de keywords obvio.

`Documentación en español | [Read in English](README.en.md)`

---

## ⚠️ Aviso legal

Esta herramienta automatiza interacciones con LinkedIn usando tu propia sesión de navegador iniciada, vía [Playwright MCP](https://github.com/microsoft/playwright-mcp). **Los Términos de Servicio de LinkedIn prohíben el acceso automatizado** a la plataforma. Usar esta herramienta puede derivar en restricciones temporales o permanentes de tu cuenta de LinkedIn. Úsala bajo tu propio riesgo. Los autores de este proyecto no se hacen responsables de ninguna consecuencia, incluida la suspensión de tu cuenta.

El proyecto **no almacena, transmite ni vende** tus credenciales de LinkedIn. Todo el scraping ocurre localmente en tu máquina usando un perfil persistente de Chromium.

---

## Qué hace

Dado tu CV base en formato **YAML** (`input/base_cv.yaml`, validado contra
`schema/base_cv.schema.json`), el sistema:

1. **Extrae** — se conecta a tu sesión de LinkedIn vía Playwright MCP (con `--user-data-dir` persistente para no tener que reloguearte), navega a la página de "Empleos guardados" y obtiene de cada oferta: título, empresa, ubicación y descripción completa. También puedes pasar **cualquier URL de LinkedIn** (no solo ofertas guardadas) con `--job <url>` y el scraper la procesa directamente.
2. **Resume la oferta** — un pase del LLM convierte la descripción cruda en un resumen estructurado (`requisitos_duros` / `skills_deseadas` / `funciones_clave`), cacheado una sola vez por oferta. Es lo único que viaja a los pases siguientes (gran ahorro de tokens).
3. **Adapta** — llama a un LLM para reescribir el CV alineándolo de forma natural
   con la oferta, sin inventar skills ni copiar frases literales. El `Perfil
   Profesional` se reescribe para cada oferta; los bullets de Experiencia y
   Proyectos se parafrasean y reordenan por relevancia; las habilidades se
   priorizan (lo relevante primero) y pueden omitirse items sueltos
   irrelevantes, siempre conservando todas las categorías del CV base.
4. **Evalúa** — un segundo pase del LLM revisa el CV adaptado contra la oferta y tu CV base, marcando alucinaciones o copias literales (configurable: `ENABLE_EVALUATION=false` lo desactiva).
5. **Repara** — si el evaluador encontró problemas semánticos, un tercer pase corrige solo lo marcado.
6. **Renderiza** — Jinja2 produce `cv.html` y Playwright genera `cv.pdf`.
7. **Revisión opcional** — `python run.py review <job_slug>` abre el CV en tu navegador para editarlo a mano y regenerar el PDF en vivo.

Para cada oferta se genera una carpeta (anidada por fecha):

```
output/
└── 2026-07-23/
    └── senior-data-engineer_acme/
        ├── cv.pdf
        ├── cv.html
        ├── analysis.json
        ├── evaluation.json
        └── job_description.txt
```

El pipeline es **incremental**: al re-ejecutar solo procesa las ofertas nuevas. Las ya procesadas se saltan a menos que pases `--force`.

---

## Instalación

### Opción A — Con Claude Code u Opencode (recomendado)

Si ya usas Claude Code u Opencode, la instalación es simple:

1. **Copia el link del repo**: `https://github.com/aaccasanih-wq/auto-tailored-cv`
2. **Pégalo en Claude Code u Opencode** y dile: *"Clona este repo e instala todo el proyecto"*
3. El asistente ejecutará los pasos de la Opción B por vos automáticamente.

Para que el skill funcione desde cualquier carpeta (no solo dentro del repo), corre después:

**macOS / Linux:**
```bash
./scripts/install_skill.sh
```

**Windows (PowerShell):**
```powershell
.\scripts\install_skill.ps1
```

Esto copia los skills a `~/.claude/skills/` y `~/.config/opencode/skills/` para que estén siempre disponibles. Una vez instalado, podés decirle a Claude u Opencode desde cualquier carpeta: *"generá el CV para la oferta https://www.linkedin.com/jobs/view/123/"* y él corre el pipeline por vos. Para cambios puntuales en tus datos (agregar una habilidad, una experiencia, un proyecto...), decile *"editá mi CV base"* (`/editar-cv`).

### Opción B — Manual (sin Claude Code ni Opencode)

#### Requisitos

- **macOS, Windows o Linux** (probado en macOS 14+; Windows 10/11 y Ubuntu deberían funcionar sin cambios)
- **Python 3.9+** — [descargar](https://www.python.org/downloads/)
- **Node.js 18+** — [descargar](https://nodejs.org/) (necesario para Playwright MCP vía `npx`)
- **Un proveedor de LLM** con endpoint OpenAI-compatible. Por defecto: [OpenCode Go](https://opencode.ai/auth) con DeepSeek V4 Flash. También funciona con DeepSeek directo, OpenRouter, etc.

#### Paso a paso

**1. Clonar el repo**

```bash
git clone https://github.com/aaccasanih-wq/auto-tailored-cv.git
cd auto-tailored-cv
```

**2. Crear entorno virtual e instalar dependencias**

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

**3. Configurar `.env`**

```bash
cp .env.example .env
```

Abre `.env` y completa:
- `LLM_API_KEY` — tu API key del proveedor LLM (obligatorio)
- `LLM_BASE_URL` — endpoint del proveedor (por defecto `https://opencode.ai/zen/go/v1`)
- `LLM_MODEL_TAILOR` y `LLM_MODEL_EVALUATOR` — modelos a usar (por defecto `deepseek-v4-flash`)

**4. Colocar tu CV base**

Tu CV base vive en `input/base_cv.yaml` (formato YAML validado contra
`schema/base_cv.schema.json`). Tienes dos formas de generarlo:

- **(a) Con tu agente de código** (recomendado, sin escribir YAML a mano):
  pega tu CV actual en PDF o Word y dile algo como:
  *"generá mi CV base a partir de este PDF"*. El agente lee el schema + el
  ejemplo, mapea tus secciones, escribe `input/base_cv.yaml` y lo valida por
  vos.
- **(b) Con cualquier chat de IA**: copia el contenido de
  [`PROMPT_PARA_TU_CV.md`](PROMPT_PARA_TU_CV.md), pega tu CV y guarda el YAML
  resultante en `input/base_cv.yaml`.

En ambos casos, valida el archivo con:

```bash
python scripts/validate_base_cv.py input/base_cv.yaml
```

> ⚠️ El **layout visual** de tu CV original (columnas, íconos, foto, colores)
> **no se conserva** — solo el contenido. El PDF final siempre usa el único
> template Harvard del repo. Es intencional: garantiza un look consistente
> entre todos los usuarios.
>
> Como referencia, `schema/example.yaml` muestra un CV completo con los 3
> tipos de sección, y `python scripts/build_base_cv.py` genera una plantilla
> con placeholders.

**5. Loguearte en LinkedIn (una sola vez)**

```bash
python run.py login
```

Esto abre una ventana de Chromium visible. Inicia sesión en LinkedIn con tu cuenta. Cuando termines, **cierra la ventana del navegador** (Cmd+Q en macOS, Alt+F4 en Windows). Las cookies se persisten en `.playwright-profile/` para que las próximas corridas sean headless (sin ventana visible).

**6. ¡Listo!**

```bash
python run.py all
```

---

## Uso

```bash
# Pipeline completo (extrae todas las ofertas guardadas, adapta cada una, renderiza PDF)
python run.py all

# Scrapear + adaptar UNA oferta específica (cualquier URL de LinkedIn, guardada o no)
python run.py all --job https://www.linkedin.com/jobs/view/4431977634/ --force

# Solo las ofertas que faltan (incremental)
python run.py all --new

# Solo N ofertas
python run.py all --limit 1

# Simulacro (sin llamar al LLM)
python run.py all --dry-run

# Listar los CVs ya generados
python run.py list

# Editar un CV en el navegador + regenerar PDF
python run.py review 2026-07-23/senior-data-engineer_acme

# Loguearse en LinkedIn (solo la primera vez)
python run.py login
```

Al terminar, `python run.py all` imprime la ruta de cada `cv.pdf` generado + un recordatorio de que `review <job_slug>` está disponible si algún resultado no te convence.

### Comandos disponibles

| Comando | Para qué |
|---|---|
| `python run.py all [flags]` | Pipeline completo: extrae + adapta + renderiza PDF |
| `python run.py extract [--job <url>]` | Solo scrapea LinkedIn → `jobs/*.json` |
| `python run.py tailor [flags]` | Solo adapta ofertas ya extraídas (sin scrape) |
| `python run.py list` | Lista los `job_slug` disponibles para `review` |
| `python run.py review <job_slug>` | Servidor local para editar el CV en el navegador |
| `python run.py login` | Abre Chromium para loguearse en LinkedIn una vez |

### Usarlo con Claude Code u Opencode

Si instalaste el skill (Opción A arriba), simplemente dile en lenguaje natural:

- *"Generá el CV para la oferta https://www.linkedin.com/jobs/view/123/"*
- *"Generá CVs para todas mis ofertas guardadas"*
- *"Solo las que faltan"*
- *"Regenerá el de [url] que no me convenció"*
- *"Listá los CVs que ya generé"*
- *"Editá el de [job_slug] en el navegador"*
- *"Convertime mi CV a base_cv.yaml"* / *"generá mi CV base a partir de este PDF"* —
  el agente lee `schema/base_cv.schema.json` + `schema/example.yaml`, mapea tus
  secciones reales a los 3 tipos (`entry_block` / `simple_list` / `text_block`),
  escribe `input/base_cv.yaml`, lo valida con `scripts/validate_base_cv.py` y
  autocorrige hasta que pase — sin que tengas que interpretar errores.
- *"Editá mi CV base"* / *"agregá X a mis habilidades"* (`/editar-cv`) —
  cambios puntuales sobre `input/base_cv.yaml` ya creado (skills, categorías,
  experiencias, proyectos, bullets, orden de secciones), siempre con
  validación automática. El orden de secciones del YAML es el orden del PDF.

El asistente traduce tu pedido a los flags correctos del CLI y lo ejecuta por vos.

---

## Configuración (`.env`)

| Variable | Default | Descripción |
|---|---|---|
| `LLM_API_KEY` | — | API key del proveedor LLM (obligatoria) |
| `LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` | Endpoint OpenAI-compatible |
| `LLM_MODEL_TAILOR` | `deepseek-v4-flash` | Modelo para el pase de reescritura |
| `LLM_MODEL_EVALUATOR` | `deepseek-v4-flash` | Modelo para evaluación + reparación |
| `LLM_REQUEST_TIMEOUT` | `120` | Timeout por request HTTP al LLM (segundos) |
| `SCRAPER_BACKEND` | `playwright` | `playwright` (default) o `browsermcp` (legacy) |
| `LINKEDIN_SAVED_JOBS_URL` | `https://www.linkedin.com/my-items/saved-jobs/` | URL de empleos guardados |
| `BASE_CV_PATH` | `input/base_cv.yaml` | Ruta a tu CV base en YAML |
| `PREFERENCES_PATH` | `input/preferences.txt` | Preferencias personales del candidato (opcional) |
| `ENABLE_EVALUATION` | `true` | Si `false`, salta evaluador + reparador (más barato, pero nadie detecta alucinaciones ni copiado literal de la oferta) |
| `OUTPUT_DIR` | `output` | Dónde se escriben los CVs |
| `REVIEW_HOST` | `localhost` | Host del servidor de revisión |
| `REVIEW_PORT` | `8420` | Puerto del servidor de revisión |

Copia `.env.example` a `.env` y completa tus valores. Para cambiar de proveedor LLM, solo cambia `LLM_BASE_URL`, `LLM_API_KEY` y `LLM_MODEL_*`.

Si la API devuelve `429 GoUsageLimitError`, el tier mensual se agotó. Cambiá la `LLM_API_KEY` a otra cuenta o pasá a pay-as-you-go.

---

## Prompts editables (`prompts/`)

Los system prompts de cada pase viven en archivos de texto plano, legibles y
editables sin tocar código:

```
prompts/
├── tailor_system.txt
├── evaluator_system.txt
├── repair_system.txt
└── job_summarizer_system.txt
```

Para personalizar un prompt, creá `prompts/<nombre>.override.txt` (gitignored):
se usa en lugar del default y sobrevive a `git pull` sin conflictos de merge.

## Preferencias personales (`input/preferences.txt`, opcional)

Si querés que el LLM siga reglas tuyas (ej. *"el resumen debe empezar con 'En
búsqueda de un puesto en...'"*), escribilas en `input/preferences.txt` (texto
plano; las líneas que empiezan con `#` se ignoran). Ver la plantilla comentada
en `input/preferences.example.txt`. Se inyectan en los tres pases como
"INSTRUCCIONES PERSONALES DEL CANDIDATO", siempre subordinadas a las reglas
críticas (no inventar datos, no copiar literal de la oferta).

---

## Seguridad / privacidad

- `.env`, `input/base_cv.yaml`, `input/preferences.txt` y `.playwright-profile/` están gitignored — ni tu CV ni tus cookies salen de tu máquina vía git.
- El scraping usa tu sesión real de Chromium — ninguna contraseña se almacena en el proyecto.
- Las URLs del CV base están protegidas: no aparecen en los prompts del LLM y se reinsertan byte-identical después del adaptador.
- El texto de las ofertas de LinkedIn se trata como **datos no confiables**: el único pase que lo procesa crudo (el job summarizer) declara explícitamente que nunca es una instrucción a seguir (mitigación de prompt injection).
- Los prompts van a tu proveedor LLM con la política de retención que tenga (OpenCode Go: cero retención).

---

## Licencia

MIT — ver [`LICENSE`](LICENSE).
