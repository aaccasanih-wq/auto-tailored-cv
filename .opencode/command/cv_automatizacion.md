---
name: cv_automatizacion
description: Genera, tailoriza, actualiza o revisa CVs en PDF para ofertas laborales de LinkedIn, y convierte un CV existente (PDF/Word) al formato base_cv.yaml. Activar cuando el usuario diga: CV, curriculum, tailorizar CV, generar CV, revisar CV, auto-tailored-cv, LinkedIn, oferta laboral, run.py all, python run.py, convertir mi CV, base_cv.yaml. La skill asume que el proyecto auto-tailored-cv es el directorio de trabajo actual (cwd); si no lo es, hacé cd ahí primero.
---

# cv_automatizacion — skill para Claude Code / Opencode

## Conversión de tu CV actual (PDF/Word/texto) → `input/base_cv.yaml`

Cuando el usuario pida **"convertime mi CV a base_cv.yaml"**, **"generá mi CV
base a partir de este PDF"** o similar (sin pedir una oferta aún), ejecutá
este procedimiento paso a paso:

1. **Leer la referencia**: `schema/base_cv.schema.json` y `schema/example.yaml`.
2. **Leer el CV que el usuario adjuntó** (PDF/Word/texto) y mapear cada sección
   real a uno de los 3 tipos genéricos (tabla de equivalencias):
   - `entry_block` → Experiencia, Educación, Proyectos, Certificaciones,
     Publicaciones, Voluntariado (estructura "qué / dónde / cuándo / bullets").
   - `simple_list` → Habilidades, Idiomas, Herramientas, Premios (items sueltos).
   - `text_block` → Resumen / Perfil / Objetivo (un solo bloque de texto).
3. **Escribir `input/base_cv.yaml`** respetando el schema exacto.
4. **Validar automáticamente**:
   ```bash
   python scripts/validate_base_cv.py input/base_cv.yaml
   ```
5. **Si falla**: corregir el YAML y repetir el paso 4 hasta que valide — sin
   pedirle al usuario que interprete el error.
6. Recién entonces avisarle que su CV base está listo y que puede pedir
   *"generá el CV para la oferta <url>"*.

> ⚠️ El **layout visual** del CV original (columnas, íconos, foto, colores)
> **no se conserva** — solo el contenido. El PDF final siempre usa el único
> template Harvard del repo. Es intencional; no lo presente como una pérdida
> accidental.

Si el usuario no tiene Claude Code/Opencode, la alternativa es `PROMPT_PARA_TU_CV.md`
(pegarlo en cualquier chat de IA junto a su CV).

---

## Generación / tailorización de CVs para ofertas

Cuando el usuario pida generar, tailorizar, actualizar o revisar sus CVs para
ofertas laborales de LinkedIn, ejecutá en bash desde la raíz del proyecto
`auto-tailored-cv` (si no estás en ese directorio, hacé `cd` ahí primero):

    python run.py all [URL] [--new] [--job <url>] [--force] [--limit N] [--dry-run] [--legacy-docx]

`URL` es un **argumento posicional opcional** y alias de `--job <url>`. Los
dos comandos siguientes son equivalentes:

    python run.py all https://www.linkedin.com/jobs/view/123/ --force
    python run.py all --job https://www.linkedin.com/jobs/view/123/ --force

Interpretá el pedido del usuario para armar los flags:

- "solo las nuevas" / "las que faltan" / "no repitas las que ya hice" → `--new`
- "esta oferta en particular: <url>" → pasá el `<url>` como **posicional** o
  vía `--job <url>`, y agregá `--force` para regenerar aunque ya exista. El
  `<url>` puede ser **cualquier** URL de LinkedIn jobs (guardada o no, de
  búsqueda o de saved-jobs). No requiere que la oferta esté "guardada" en la
  cuenta del usuario.
- "vuelve a generar aunque ya exista" / "regenerar todos" / "no saltees
  ninguno" → `--force`
- "no llames al LLM, solo revisa qué haría" / "dry run" / "simulacro" →
  `--dry-run`
- "solo N ofertas" / "los primeros N" → `--limit N`

> **Dedup automático**: el pipeline mantiene un registro por `job_id`
> (`jobs/_index.json`). Si el usuario pega un link de una oferta que ya tiene
> CV (aunque sea con otra variante de URL: guardada, recomendada, de búsqueda),
> el CLI responde *"Ya generaste un CV para esta oferta ... agregá --force"* y
> NO gasta llamadas LLM. Si el usuario insiste en regenerar, agregá `--force`.

## ⚠️ Invariante crítico — scope del `--force`

- `tailor --force` o `all --force` **SIN** el URL posicional / `--job` re-tailoriza
  TODOS los jobs cacheados en `jobs/*.json`. Antes de disparar el LLM, el CLI
  imprime `About to (re)tailor N jobs ... Proceed? [y/N]`. En runs no
  interactivos (stdin cerrado / EOF) aborta con rc=1.
- **Cuando el usuario pidió UNA sola oferta, SIEMPRE pasá el URL** (posicional
  o `--job`). Nunca omitas el URL aunque la extracción ya haya corrido
  (extraer de nuevo 1 sola oferta cuesta ~15s; no es motivo para saltear).
- **Tras un timeout del shell**: re-ejecutá EL MISMO comando con timeout mayor.
  NO "optimices" cambiando a `tailor --force` sin URL — eso re-tailorizaría
  todas las ofertas cacheadas.
- Si el usuario confirma explícitamente "regenerá TODAS" / "todas las ofertas":
  podés correr `tailor --force --yes` (el `--yes` saltea el prompt `[y/N]`).
- **Antes de cualquier `--force` real**, corré primero `--dry-run` y contá
  las filas `[N/M]`. `M` debe igualar la cantidad que el usuario pidió. Si
  `M>1` y el usuario pidió una sola oferta, STOP: agregá el URL.

## Comandos disponibles

| Comando | Para qué sirve |
|---|---|
| `python run.py all [url] [flags]` | Pipeline completo: extrae + tailoriza + renderiza PDF |
| `python run.py extract [url] [--job <url>]` | Solo scrapea LinkedIn → `jobs/*.json` |
| `python run.py tailor [url] [flags]` | Solo tailoriza jobs ya extraídos (sin scrape) |
| `python run.py list` | Lista los `job_slug` disponibles para `review` |
| `python run.py review <job_slug>` | Servidor local para editar el CV en el navegador |
| `python run.py login` | Abre Chromium headed para loguearse en LinkedIn una vez |

`url` es un argumento posicional opcional, alias de `--job <url>` (presente
en `all`, `tailor` y `extract`). `--yes` saltea el prompt `[y/N]` que aparece
cuando `--force` sin URL tocaría >1 job.

## Pipeline (4 etapas)

1. **extract** — scrapea LinkedIn con Playwright MCP usando el perfil
   persistente (`.playwright-profile/`). El scraper hace clic en el botón
   "...más" / "See more" para capturar la descripción completa. Cuando se
   pasa `--job <url>` con una URL de job, scrapea esa oferta directamente
   (una sola navegación) sin pasar por el listado de saved-jobs.
2. **summarize_job** — un pase del LLM convierte la descripción cruda de la
   oferta en un resumen estructurado (`requisitos_duros` / `skills_deseadas`
   / `funciones_clave`), cacheado como `job_summary.json` (una sola vez por
   oferta; se recalcula solo con `--force`). La descripción cruda NO vuelve a
   viajar a los pases siguientes. Es el único pase que procesa el texto de la
   oferta, por lo que su system prompt lo trata como datos no confiables.
3. **tailor** — llama al LLM configurado (DeepSeek V4 Flash por defecto en
   OpenCode Go). Hasta tres llamadas por job:
   - **tailor** (siempre): reescribe el CV alineándolo a la oferta.
   - **evaluate** (siempre, salvo `ENABLE_EVALUATION=false`): revisa
     alucinación, copia verbatim, formato.
   - **repair** (solo si el evaluator halló issues semánticos): corrige
     solo lo marcado. Los issues determinísticos (`url_tampered`, `format`)
     se filtran y NO disparan repair — ya los maneja el código.
4. **render** — Jinja2 produce `cv.html` y Playwright genera `cv.pdf`.

Cada corrida es incremental: los jobs ya tailorizados se saltean a menos que
pases `--force`.

## Estructura de carpetas de output

Los CVs se generan bajo `output/` anidados por fecha:

    output/
    └── 2026-07-23/
        └── practicante-profesional-de-ia_canvia/
            ├── cv.pdf
            ├── cv.html
            ├── analysis.json
            ├── analysis_repaired.json   (solo si hubo repair)
            ├── evaluation.json
            ├── job_summary.json         (resumen estructurado de la oferta)
            ├── job_description.txt
            └── cv_style.css

El `job_slug` para `review` puede pasarse en cualquiera de estas formas:
- `2026-07-23/practicante-profesional-de-ia_canvia` (forma completa)
- `practicante-profesional-de-ia_canvia` (bare slug — busca por fecha)
- `2026-07-23_practicante-profesional-de-ia_canvia` (legacy flat)

Usá `python run.py list` para ver todos los `job_slug` disponibles.

## Revisión editable

Si el usuario quiere revisar/editar un CV antes del PDF final, o dice que no
le convenció el resultado para un puesto específico, corré:

    python run.py review <job_slug>

Eso levanta un servidor local en `localhost:8420` (configurable via `.env`)
y abre el navegador con el `cv.html` editable. El botón "Guardar y generar
PDF" sobreescribe el HTML y regenera el PDF en vivo.

## Cómo listar los job_slugs disponibles

Si el usuario pregunta qué CVs ya generó o cuáles puede revisar, corré:

    python run.py list

Cada fila es un `job_slug` válido para `review`.

## Output esperado

Al terminar, mostrá al usuario:
- la ruta de cada `cv.pdf` generado (ej.
  `output/2026-07-23/practicante-profesional-de-ia_canvia/cv.pdf`)
- la ruta del `cv.html` editable por si quiere tocar algo manualmente
- un recordatorio de que `review <job_slug>` está disponible si no le
  convence el resultado

Ejemplo de mensaje final:

> Listo. Generé 1 CV:
> - output/2026-07-23/practicante-profesional-de-ia_canvia/cv.pdf
>
> Algo no te convence? Editá en el navegador con
> `python run.py review 2026-07-23/practicante-profesional-de-ia_canvia`.

## Modelos y residencia de datos

- El LLM por defecto es DeepSeek V4 Flash servido desde OpenCode Go
  (`https://opencode.ai/zen/go/v1`). Cualquier endpoint OpenAI-compatible
  funciona cambiando `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_*` en
  `.env`. No requiere reiniciar nada — el pipeline lee `.env` en cada
  corrida.
- Si la API devuelve `429 GoUsageLimitError`, el tier mensual del workspace
  se agotó. Sugerí al usuario cambiar de cuenta (nueva `LLM_API_KEY`) o
  pasar a un modelo free / al tier pay-as-you-go (`LLM_BASE_URL=
  https://opencode.ai/zen/v1` + `LLM_MODEL_*=deepseek-v4-pro` por ejemplo).

## Prerrequisitos que el usuario ya debe tener (no intentar arreglarlos)

- `.env` con `LLM_API_KEY` válida.
- `.playwright-profile/` con sesión de LinkedIn ya logueada (si el
  scraper devuelve login-wall, decile al usuario que corra
  `python run.py login` una vez y se loguee manualmente).
- Playwright Chromium instalado: `python3 -m playwright install chromium`.
