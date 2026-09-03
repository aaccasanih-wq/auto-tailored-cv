---
name: cv_automatizacion
description: Genera, tailoriza, actualiza o revisa CVs en PDF para ofertas laborales de LinkedIn, y convierte un CV existente (PDF/Word) al formato base_cv.yaml. Activar cuando el usuario diga: CV, curriculum, tailorizar CV, generar CV, revisar CV, auto-tailored-cv, LinkedIn, oferta laboral, run.py all, python run.py, convertir mi CV, base_cv.yaml. La skill asume que el proyecto auto-tailored-cv es el directorio de trabajo actual (cwd); si no lo es, hacé cd ahí primero.
---

# cv_automatizacion — skill para Claude Code / Opencode

## ⚠️ LEER PRIMERO — acceso a herramientas y entorno

- **Tenés acceso a herramientas de shell/bash para ejecutar comandos en la
  laptop del usuario.** En Claude Desktop esto se habilita vía el conector
  **Desktop Commander**; en Claude Code / Opencode ya tenés la tool Bash/Shell.
  **Usalas directamente** para correr este pipeline — no dudes, no pidas
  permiso y no asumas que no tenés acceso: es la única forma de generar el CV.
- **El proyecto vive en** `/Users/axelaaronccasanihuachua/Desktop/Data_Science/auto-tailored-cv`.
  Antes de correr cualquier comando, `cd` a ese directorio (Desktop Commander
  suele arrancar en otra carpeta). Verificá que estás ahí con `ls run.sh`.
- **El virtualenv YA existe** en `.venv/` con todas las dependencias instaladas
  (playwright 1.60.0 pineado para macOS 12). NO lo crees desde cero: usá el
  wrapper `./run.sh`, que lo levanta solo y, si hiciera falta, corre
  `scripts/bootstrap.sh` para rearmarlo de forma idempotente.

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

Para cambios puntuales posteriores sobre el base ya creado (agregar una
habilidad/herramienta, crear/quitar una categoría, añadir una experiencia o
un proyecto, editar bullets, reordenar secciones...), ver la skill
`editar_cv` (comando `/editar-cv` en Opencode): el procedimiento vive ahí.

---

## Generación / tailorización de CVs para ofertas

Cuando el usuario pida generar, tailorizar, actualizar o revisar sus CVs para
ofertas laborales de LinkedIn, ejecutá en bash desde la raíz del proyecto
`auto-tailored-cv` (si no estás en ese directorio, hacé `cd` ahí primero):

    ./run.sh all [--new] [--job <url>] [--force] [--limit N] [--dry-run] [--legacy-docx]

> **Entorno (IMPORTANTE):** usá SIEMPRE el wrapper `./run.sh`, NO `python run.py`.
> `run.sh` auto-ejecuta `scripts/bootstrap.sh` solo cuando hace falta (venv
> faltante, deps rotas, o playwright distinto al pineado `==1.60.0`) y agrega
> `/usr/local/bin` al PATH para que `npx` funcione. Si `./run.sh` falla con
> errores de módulos/path, corré `bash scripts/bootstrap.sh` manualmente y
> mostrá su salida — nunca intentes "arreglar" pip/playwright a mano.

### Camino rápido para empleos guardados

Si el pedido es simplemente "generá los CV para mis empleos guardados", ejecutá
`./run.sh all` directamente. No inspecciones ni cuentes primero `jobs/`:
`jobs/` y `jobs/_index.json` son caches históricos y conservan ofertas que el
usuario ya desguardó. El primer `extract` de `all` es la única fuente de verdad
del número y alcance actual de LinkedIn. Al terminar, informá las rutas de los
`cv.pdf` generados. Si un job falla por una respuesta inválida del proveedor
LLM, reintentá esa oferta una vez; si vuelve a fallar, reportá el error al
usuario en vez de entrar a depurar o reescribir los scripts del proyecto.

Interpretá el pedido del usuario para armar los flags:

- "solo las nuevas" / "las que faltan" / "no repitas las que ya hice" → `--new`
- "esta oferta en particular: <url>" → `--job <url>` (y agregá `--force` para
  regenerar aunque ya exista). El `<url>` puede ser **cualquier** URL de
  LinkedIn jobs (guardada o no, de búsqueda o de saved-jobs). No requiere
  que la oferta esté "guardada" en la cuenta del usuario.
- "vuelve a generar aunque ya exista" / "regenerar todos" / "no saltees
  ninguno" → `--force`
- "no llames al LLM, solo revisa qué haría" / "dry run" / "simulacro" →
  `--dry-run`
- "solo N ofertas" / "los primeros N" → `--limit N`
- "la última oferta (guardada)" / "la que guardé más recientemente" →
  `./run.sh extract` (fresco) y después `./run.sh tailor --last 1`
- "las N últimas ofertas guardadas" / "las 6 que guardé hoy" →
  `./run.sh extract` y después `./run.sh tailor --last N`
- "solo las pendientes de generar CV" → `--new`
- **"generá el CV para estas indicaciones/descripción de esta oferta"** (texto
  pegado de otra plataforma, NO un link de LinkedIn) → usá el subcomando
  `manual`. Ver la sección "Oferta pegada (no LinkedIn)" más abajo.

### IMPORTANTE — "última oferta guardada" (regla anti-arbitrariedad)

El pipeline **no puede saber** cuál es la última oferta guardada leyendo el
cache viejo: `saved_at_iso` solo se llena en extracciones nuevas (parsea el
"Guardado hace X días" / "Saved N days ago" de la página de saved-jobs, y como
fallback guarda el orden de listado `saved_order`, porque LinkedIn ordena la
lista por más reciente primero). Por lo tanto:

1. **SIEMPRE corré primero `./run.sh extract`** para refrescar el cache
   con `saved_at_iso`/`saved_order`; `--last N` usa el manifiesto de esa
   extracción y no mezcla archivos históricos sin orden válido.
2. Después usá `./run.sh tailor --last N`.
3. **Verificá con `--dry-run` primero** (`./run.sh tailor --last N --dry-run`)
   y confirmá que el `[1/N]` que imprime es la oferta que el usuario quiere.
   No asumas ni adivines: la fecha/orden sale de los datos.
4. Si `--last N` no te da la oferta esperada, el cache está desactualizado:
   volvé a `extract` o preguntale al usuario por el link exacto.

> **Dedup automático**: el pipeline mantiene un registro por `job_id`
> (`jobs/_index.json`). Si el usuario pega un link de una oferta que ya tiene
> CV (aunque sea con otra variante de URL: guardada, recomendada, de búsqueda),
> el CLI responde *"Ya generaste un CV para esta oferta ... agregá --force"* y
> NO gasta llamadas LLM. Si el usuario insiste en regenerar, agregá `--force`.

## Comandos disponibles

| Comando | Para qué sirve |
|---|---|
| `./run.sh all [flags]` | Pipeline completo: extrae + tailoriza + renderiza PDF |
| `./run.sh extract [--job <url>]` | Solo scrapea LinkedIn → `jobs/*.json` |
| `./run.sh tailor [flags]` | Solo tailoriza jobs ya extraídos (sin scrape) |
| `./run.sh manual [--description ...]` | Tailoriza desde una descripción de oferta pegada (no LinkedIn) |
| `./run.sh list` | Lista los `job_slug` disponibles para `review` |
| `./run.sh review <job_slug>` | Servidor local para editar el CV en el navegador |
| `./run.sh login` | Abre Chromium headed para loguearse en LinkedIn una vez |

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

## Oferta pegada (no LinkedIn) — comando `manual`

Cuando el usuario pegue la descripción/indicaciones de una oferta publicada en
**otra plataforma** (no un link de LinkedIn), NO uses `extract` ni `tailor`
(esos dependen del scrape de LinkedIn). Usá el subcomando `manual`:

    ./run.sh manual --title "Data Engineer" --company "Acme" \
        --description-file /tmp/oferta.txt --force

El texto de la oferta puede llegar por tres vías (en orden de preferencia):
1. **`--description-file <path>`** — escribí la descripción a un archivo (p.ej.
   con `cat > /tmp/oferta.txt <<'EOF' … EOF`) y pasá la ruta. Ideal para textos largos.
2. **`--description "<texto>"`** — inline para textos cortos.
3. **stdin** — `./run.sh manual --title "…" --company "…" < /tmp/oferta.txt`.

`--title` y `--company` son opcionales: si faltan, el título se deriva de la
primera línea del texto y la empresa queda como "empresa". Usalos cuando puedas
para que el nombre de la carpeta de output sea legible. Este subcomando corre el
mismo pipeline (summarize → tailor → evaluate → repair → render) que `tailor`,
pero sin scrapear LinkedIn.

## Cómo editar el CV a mano (sin correr ningún pipeline)

La fuente editable **no es el PDF** — es el `cv.html` que vive en la carpeta de
output de cada oferta (`output/<fecha>/<slug>/cv.html`). El PDF es solo su
impresión. Hay tres formas de tocar el resultado:

- **A. Editar como un .txt (cero comandos):** abrí `cv.html` con cualquier
  editor (VS Code, TextEdit…), cambiá el texto entre etiquetas y guardá. Para
  regenerar el PDF, abrí ese `cv.html` en el navegador (doble clic) y presioná
  **Cmd+P → Guardar como PDF** (el CSS está copiado al lado y el botón de
  revisión se oculta en la impresión).
- **B. Editar directo en el navegador (cero comandos):** doble clic en `cv.html`
  → los campos son editables inline (`contenteditable`) → Cmd+P → Guardar como PDF.
- **C. Flujo con auto-guardado (un comando):** `./run.sh review <job_slug>` abre
  `localhost:8420`; editás en el navegador y el botón "Guardar y generar PDF"
  re-escribe el HTML y regenera el PDF solo.

Si el usuario dice "editá el CV" sin más, lo más simple es decirle que abra el
`cv.html` (o usar `review` si prefiere el auto-guardado). No hace falta que vos
reescribas el HTML salvo que el usuario lo pida.

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

Usá `./run.sh list` para ver todos los `job_slug` disponibles.

## Revisión editable

Si el usuario quiere revisar/editar un CV antes del PDF final, o dice que no
le convenció el resultado para un puesto específico, corré:

    ./run.sh review <job_slug>

Eso levanta un servidor local en `localhost:8420` (configurable via `.env`)
y abre el navegador con el `cv.html` editable. El botón "Guardar y generar
PDF" sobreescribe el HTML y regenera el PDF en vivo.

## Cómo listar los job_slugs disponibles

Si el usuario pregunta qué CVs ya generó o cuáles puede revisar, corré:

    ./run.sh list

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
> `./run.sh review 2026-07-23/practicante-profesional-de-ia_canvia`.

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
  `./run.sh login` una vez y se loguee manualmente).
- Playwright Chromium instalado: el wrapper `./run.sh` / `scripts/bootstrap.sh`
  lo descargan automáticamente con la versión pineada (`playwright==1.60.0`,
  la última compatible con macOS 12). No lo instales a mano.
