# AGENTS.md — guide for opencode/AI agents working on this repo

## What this project is

`auto-tailored-cv` reads a base CV in **HTML** (text + hyperlinks, no images) and
a list of saved LinkedIn jobs, then rewrites the CV naturally for each job
using any OpenAI-compatible LLM (default: OpenCode Go / GLM 5.2), and outputs
both `.html` and `.pdf`. The pipeline is:

```
extract  →  profile  →  tailor  →  evaluate  →  repair  →  render_html  →  [review opcional]  →  render_pdf
```

## Commands you should run before declaring a task done

- `pytest tests/ -v` — must be 100% passing.
- `python3 run.py --help` — smoke check the CLI doesn't crash.
- `python3 run.py tailor --dry-run` — runs the cache logic without LLM calls
  (will report "no saved jobs" if Playwright MCP isn't connected, which is fine).

## Hard rules

- **Never commit `.env`, `input/*.html`, `.playwright-profile/`.** The CV is
  personal data, and the Playwright profile carries LinkedIn cookies — both
  are secrets.
- **Never commit `jobs/` or `output/`.** They are runtime, regenerable.
- Don't install the `mcp` PyPI package — it requires Python 3.10+; this
  machine has Python 3.9. We use `src/extract/mcp_stdio.py` instead.
- Don't add `linkedin-mcp-server` (the stickerdaniel one) — out of scope. We
  talk to **Playwright MCP** (`@playwright/mcp`) directly via our JSON-RPC
  stdio client. Playwright MCP is preferred over Browser MCP because:
  (a) native `browser_wait_for` auto-wait resolves the historical problem of
  LinkedIn pages that didn't finish loading before the snapshot;
  (b) `--user-data-dir` persists the LinkedIn session;
  (c) it reuses the same Chromium build that `pdf_renderer.py` installs.
  Browser MCP is kept as an optional fallback behind `--scraper browsermcp`.
- No new LLM client library. We use the official `openai` SDK pointed at any
  OpenAI-compatible endpoint (LLM_BASE_URL); defaults to OpenCode Go.
- **URLs / hyperlinks are protected end-to-end**: they are extracted into
  structured `{texto, url}` objects by `src/profile/cv_reader.py`, OMITTED
  from every prompt sent to the LLM (see `prompts._strip_enlaces_for_llm`
  and the explicit instruction in `TAILOR_SYSTEM`), and re-injected
  byte-identical into the tailored JSON after the tailor / repair passes
  (see `cv_rewriter._reinject_enlaces`). A buggy LLM that emits URL fields
  will have them overwritten by the base values on every run. Reinjection
  matches entries BY TITULO (not by index) so that reordering or removal of
  `proyectos` entries still maps each tailored entry to the correct base URL.
- **Header centered** at the top of the page (`.header { text-align: center }`
  in `cv_style.css`): name, contact line, and tagline all centered. The base
  CV (`input/base_cv.html`) follows the same rule. Don't revert to flex
  right-aligned inadvertently.
- **Project section license**: the LLM is allowed to (a) reorder `proyectos`
  entries, (b) remove an entry entirely if it doesn't add to the job
  application, (c) edit the `descriptor` parenthetical (e.g. "(Agentic AI ·
  RAG · Automatización)" can become "(IA · Automatización · RAG)" or be set
  to empty string "" — never leave dangling empty parens "()"), (d) split
  one long bullet into several shorter ones or merge two bullets into one
  when clearer. The FIRST bullet of each project MUST describe what the
  project is/does/solves; subsequent bullets follow with implementation
  details. Never INVENT projects: every project in the output must exist in
  the base CV (validated by `_validate_shape`). See the "Section-specific
  rules" subsection of the `analysis.json` schema below.
- **Scraper "see more" click**: LinkedIn truncates long job descriptions and
  shows a "...más" / "See more" button. The scraper runs a regex
  (`SEE_MORE_RE`) over the Playwright MCP snapshot, finds the button's ref,
  calls `browser_click` to expand the description, then re-snapshots. If
  that fails, falls back gracefully.
- Most saved jobs on LinkedIn today are "Apply on company website" /
  "Respuestas gestionadas fuera de LinkedIn" listings — meaning the full
  description is hosted on an external site (Computrabajo, HiringRoom, GetOnBrd,
  …). The scraper notes this in `SavedJob.warnings =
  ["external_apply_no_description", "external_url:<url>"]` and writes a
  `[LinkedIn no aloja la descripción completa ...]` placeholder in
  `job.description`. Tailor accepts placeholder bodies but the tailored CV
  will necessarily be less specific. The user can override by editing
  `jobs/<id>.json` with a richer description by hand if needed.

## Architecture map

- `src/config.py` — `Settings` frozen dataclass loaded from `.env` via dotenv.
  LLM fields are provider-agnostic (`LLM_*`); defaults keep OpenCode Go + GLM 5.2.
- `src/profile/cv_reader.py` — reads `input/base_cv.html` with BeautifulSoup4
  into a `CVProfile` of typed sections (`educación`/`experiencia`/`proyectos`/
  `habilidades`). Every `<a href>` is captured as a protected `Enlace`.
- `src/tailor/` — three LLM passes (tailor / evaluate / repair).
- `src/tailor/llm_client.py` — wrapper over the official `openai` SDK pointed at
  any `LLM_BASE_URL`.
- `src/tailor/prompts.py` — prompt builders. The tailor / evaluator / repair
  prompts OMIT `enlaces` / `contact_enlaces` entirely and instruct the LLM
  explicitly that URLs are protected and out of scope.
- `src/tailor/cv_rewriter.py` — tailor pass. After the LLM returns, calls
  `_reinject_enlaces(tailored, base_cv)` to restore URLs from the base CV.
  Reinjection matches entries by `titulo` (not by index) so that reordering
  or removal of `proyectos` entries still maps each tailored entry to the
  correct base counterpart's URLs. `_validate_shape` enforces section-specific
  rules: flexible entry list + editable descriptor for `proyectos`, strict
  1:1 for `experiencia` / `educacion`. Flags invented projects, empty
  parentheses "()" in descriptor, and stray `enlaces` emitted by the LLM.
- `src/extract/mcp_stdio.py` — minimal JSON-RPC 2.0 over stdio (unchanged).
- `src/extract/linkedin_scraper.py` — uses Playwright MCP (default) or
  Browser MCP (`--scraper browsermcp`). Uses `browser_wait_for` with
  Spanish/English job-description markers to auto-wait for content.
  After the initial snapshot, calls `_try_click_see_more` to click the
  "...más" / "See more" button that LinkedIn renders on long job descriptions
  (truncates ~400 chars without the click), then re-snapshots.
  Persistent `--user-data-dir` via `PLAYWRIGHT_USER_DATA_DIR`.
- `src/render/html_renderer.py` — Jinja2 render of `templates/cv_template.html`
  + reused `templates/cv_style.css`. Exposes a custom `project_links_html`
  filter that merges each project's `descriptor` with its `enlaces`:
  if a link's `texto` appears in the descriptor, the text becomes an `<a href>`;
  otherwise the link is appended after a " · " separator. Empty descriptors
  produce NO parentheses (never dangle "()"). Each rewritable block carries
  `contenteditable="true" + data-field="..."`. Copies the CSS next to cv.html.
- `src/render/pdf_renderer.py` — Playwright Chromium headless render of
  `cv.html` → `cv.pdf` via `page.pdf(format="Letter", print_background=True)`.
  Atomic: emits `PdfResult{success, pdf_path, error}`.
- `src/render/legacy/docx_writer.py` + `pdf_converter.py` — legacy docx +
  LibreOffice path behind `--legacy-docx`. Use only for backwards compat.
- `src/review/server.py` — FastAPI app for `python run.py review <slug>`.
  `GET /` serves `cv.html`, `POST /save` overwrites it + kicks off PDF
  regen in a background thread. The save button in `cv_template.html` is
  hidden in `@media print`.
- `run.py` — CLI. Subcommands: `all`, `extract`, `tailor`, `review`.
  Flags: `--new`, `--force`, `--job <url>`, `--dry-run`, `--limit N`,
  `--scraper playwright|browsermcp`, `--legacy-docx`. The `all` / `tailor`
  commands do NOT pause for approval — they print each generated `cv.pdf`
  path + a reminder that `review <slug>` is available.

## analysis.json schema (the contract)

```
{
  "summary": "En búsqueda de un puesto en <cat1> · <cat2> · <cat3>",
  "sections": [
    {
      "title": "<SECTION TITLE>",
      "kind": "educacion" | "experiencia" | "proyectos" | "habilidades",
      "entries": [
        {
          "titulo": "<immutable>", "fecha": "<immutable>",
          "subtitulo": "<immutable>",
          "descriptor": "<editable for proyectos sessions; immutable elsewhere>",
          "enlaces": [{"texto": "...", "url": "..."}],   # protected
          "bullets": ["...", "..."]                        # rewritable
        }
      ],
      "table": [["label", "value"], ...]   # ONLY when kind=habilidades
    }
  ]
}
```

The LLM **never sees** `enlaces`. After tailor / repair, the orchestrator
re-injects URLs from the base CV. `_validate_shape` (called BEFORE reinjection)
flags any `enlaces` array the LLM might emit as a "url_tampered" warning.

### Section-specific rules (post v2)
- **proyectos**: entries may be REORDERED, REMOVED (if irrelevant to the job),
  and their `descriptor` field may be EDITED (e.g. "(Agentic AI · RAG ·
  Automatización)" can become "(IA · Automatización · RAG)" or emptied to "").
  Bullets within an entry may be SPLIT (one long bullet → two shorter ones) or
  MERGED if the result is clearer. The FIRST bullet of each project MUST
  describe what the project is / does / what problem it solves; subsequent
  bullets follow with implementation details (dashboard, architecture, etc.).
  Never invent projects that don't exist in the base CV (validation flags this).
- **experiencia / educacion**: strict 1:1 match with the base — same order,
  same entry count, same bullet count per entry. `descriptor` and `subtitulo`
  are immutable here, only bullets are rewritable.

## When edits are needed

- If you add a new LLM pass, route it through `llm_client.LLMClient.chat`.
- If you change the tailored JSON schema, also touch `_validate_shape` in
  `cv_rewriter.py`, `prompts.py` (schema spec in the system prompt), and
  `tests/test_tailor_pipeline.py`.
- Run `pytest tests/test_tailor_pipeline.py -v` after any prompt edit.
- For debugging Playwright MCP, run `npx -y @playwright/mcp@latest --headless
  --user-data-dir .playwright-profile` directly in a terminal to see its stderr.
  Don't mock the MCP server in tests — they would lie about the live protocol.
- For the legacy docx path (only), use `--legacy-docx` and run the
  `tests/legacy/` tests with `python-docx` + LibreOffice installed.

## Secrets handling

- Real values live ONLY in `.env` (gitignored). Committed template is
  `.env.example`.
- The Playwright persistent profile (`.playwright-profile/`) is gitignored
  because it contains LinkedIn cookies. Treat it like a secret.
- If you see a real key in any file you're about to commit, STOP and rewrite
  it to the placeholder before staging.

## State snapshot — Jul 22, 2026

After the second-day refactor.

### LLM provider (current)

- `.env` currently points at `LLM_BASE_URL=https://opencode.ai/zen/go/v1`
  (OpenCode Go subscription tier) with `LLM_MODEL_TAILOR=deepseek-v4-flash`
  and `LLM_MODEL_EVALUATOR=deepseek-v4-flash`. GLM 5.2 hit its monthly usage
  limit on the previous workspace; switching to DeepSeek V4 Flash on a
  different account resolved it. The `/go/` endpoint is rate-limited per
  workspace per month; the general `/zen/v1` endpoint is pay-as-you-go per
  token but currently unused.
- Any OpenAI-compatible provider works (DeepSeek direct, OpenRouter, etc.) —
  just edit `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL_*` in `.env`. No
  code change needed.

### Pipeline state

- LinkedIn session is persisted in `.playwright-profile/` (headed login done
  once via `python run.py login`). Subsequent runs are headless and the
  scraper reuses the cookies.
- `jobs/` holds 15 cached jobs. Each job's `json` has a `tailored: true|false`
  flag and the `tailor --new` flag uses it to skip already-processed jobs.
- `output/` has 10 generated CVs (some from the first batch with GLM 5.2,
  some from the second batch with DeepSeek V4 Flash after the v2 prompt
  refactor). The v2-rendering (centered header, project descriptor inline
  with links, removal/reordering allowed) was re-applied to all pre-existing
  analyses via a one-off re-render script (see `/tmp` history). The
  `--force` flag re-tailors from scratch with the current LLM.

### Generated CVs (output/, as of Jul 22 15:00)

CVs exist for: Senior Data Engineer @ Acme, Analista Junior Automatización @
Target Marketing, Practicante Data Analyst @ LG Electronics Perú,
Practicante de Transformación Digital @ Diners Club Perú, Asistente
Transformación de Procesos @ Prestamype, Practicante Profesional de Mejora
Continua @ Grupo Idisac, Practicante profesional de proyectos @ Inca Rail,
Trainee de Innovación @ Neo Consulting, Practicante de Proyectos @ Talana,
Practicante Profesional de Automatización de Procesos @ Newport Capital,
Practicante Profesional de Procesos @ SANNA salud, Practicante Profesional
de Productos Digitales SaaS @ Doctocliq, Practicante Pro Comercial @
apparka.

Still pending (no `analysis.json` yet): Trainee en Automatización de
Procesos y Análisis de Datos - Finanzas @ EY, Practicante Profesional de
Procesos @ Clínica Sánchez Ferrer. Run `python run.py tailor --new` to
finish them.

### Skills (for Claude Desktop / Opencode Desktop / Kimi Desktop)

- `.claude/skills/cv_automatizacion.md` — auto-loaded by Claude Desktop and
  Claude Code when opened in the project root. The skill teaches the
  assistant to translate natural-language requests like "generá el CV para
  la oferta `<url>`" into `python run.py all --job <url> --force`, etc.
- `.opencode/command/cv_automatizacion.md` — same content, exposed as the
  `/cv_automatizacion` command in Opencode Desktop / CLI.
- For Kimi Desktop (no auto-skill loader): paste the file contents into the
  custom instructions / system prompt manually.

### Test status

`pytest tests/ -v` → 136 passed, 0 failed (verified after every prompt /
renderer / scraper edit). Don't break this.