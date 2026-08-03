# AGENTS.md — guide for opencode/AI agents working on this repo

## What this project is

`auto-tailored-cv` reads a base CV in **YAML** (`input/base_cv.yaml`, validated
against `schema/base_cv.schema.json`) and a list of saved LinkedIn jobs, then
rewrites the CV naturally for each job using any OpenAI-compatible LLM
(default: OpenCode Go / DeepSeek V4 Flash), and outputs both `.html` and
`.pdf`. The base CV format is **generic** (section `type` is one of
`entry_block` / `simple_list` / `text_block`) so any person can publish/use the
project regardless of which sections their CV has. The pipeline is:

```
extract  →  summarize_job  →  tailor  →  evaluate  →  repair  →  render_html  →  [review opcional]  →  render_pdf
```

## Commands you should run before declaring a task done

- `pytest tests/ -v` — must be 100% passing.
- `python3 run.py --help` — smoke check the CLI doesn't crash.
- `python3 run.py tailor --dry-run` — runs the cache logic without LLM calls
  (will report "no saved jobs" if Playwright MCP isn't connected, which is fine).
- `python3 run.py tailor <url> --dry-run` — confirms the positional URL alias
  resolves to exactly ONE job from `jobs/` (count the dry-run rows!).
- `python3 scripts/validate_base_cv.py input/base_cv.yaml` — after any change
  to the user's base CV, confirm it still validates against the schema.

## Hard rules — CLI scope (avoids accidental bulk re-tailorization)

- `tailor` and `all` accept a **positional URL** as alias for `--job`:
  `python run.py tailor https://www.linkedin.com/jobs/view/123/ --force` —
  This targets exactly ONE cached job and NEVER triggers the bulk-confirm
  prompt. This is the **safest form to use from an LLM agent**: always include
  the URL (positional or via `--job`) when the user asked for a single offer.
- `tailor --force` **without** `--job`/positional re-tailorizes EVERY job in
  `jobs/*.json`. Since v0.2 this triggers an interactive `[y/N]` prompt on
  stdin before any LLM call — non-interactive runs (no TTY / EOF on stdin)
  abort with rc=1. Pass `--yes` to skip the prompt when you genuinely mean
  "re-tailorize all N jobs".
- Before any `--force` run, ALWAYS test scope with `--dry-run` first and count
  the `[N/M]` rows. M must equal the number the user asked for. If M>1 and the
  user asked for one offer, STOP and add `--job <url>` or the positional URL.

## Dedup / registry (jobs/_index.json)

- There is a registry `jobs/_index.json` keyed by the **canonical LinkedIn
  `job_id`** (not the URL), so different URL variants of the same offer (saved,
  recommended, search, share link with `jobPostingId=`) all resolve to ONE
  record. Each record tracks `tailored`/`status`, `cv_generated_at`, and
  `cv_pdf_path`.
- `do_extract` does a **preserving upsert**: re-extracting saved jobs refreshes
  title/company/description but NEVER resets `tailored=true` — so `all` won't
  silently re-tailorize offers that already have a CV.
- If the user pastes a link for an offer that already has a CV (by `job_id`),
  the CLI prints `Ya generaste un CV para esta oferta ... agregá --force` and
  returns **without calling the LLM**. `--force` bypasses the guard.
- `_backfill_index()` populates the registry from existing `jobs/*.json` cache
  files (idempotent), so pre-existing jobs are covered even before their next
  extraction. It runs at the start of `all` and `tailor`.

## "Última oferta guardada" (recency)

- The scraper captures when each job was saved: it parses the `Guardado hace X
  días` / `Saved N days ago` labels of the saved-jobs listing into
  `SavedJob.saved_at_iso`, and always records `saved_order` (1 = most recently
  saved, because LinkedIn orders the listing by recency).
- `--last N` processes the N most recently saved jobs: it sorts by
  `saved_at_iso` desc (falling back to `saved_order` for old caches).
- The user's cache only gets `saved_at_iso`/`saved_order` on a FRESH
  `extract`. So a query like "generá el CV para la última oferta guardada"
  MUST be answered by running `extract` first, then `tailor --last 1`, and
  verifying with `--dry-run` before spending LLM calls. Never guess "last"
  from file/job-id ordering — it is arbitrary.

## Hard rules

- **Never commit `.env`, `input/base_cv.yaml`, `input/preferences.txt`,
  `.playwright-profile/`.** The CV is personal data, and the Playwright profile
  carries LinkedIn cookies — both are secrets.
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
  structured `{label, url}` objects (`personal_info.links` and per-entry
  `links`), OMITTED from every prompt sent to the LLM (see
  `prompts._strip_links_for_llm` and the explicit instruction in
  `prompts/tailor_system.txt`), and re-injected byte-identical into the
  tailored JSON after the tailor / repair passes (see
  `cv_rewriter._reinject_links`). A buggy LLM that emits `links`/`enlaces`
  fields will have them overwritten by the base values on every run.
  Reinjection matches entries BY `heading` (not by index) so that reordering
  or removal of entries in `reorderable: true` sections still maps each
  tailored entry to the correct base links.
- **Header centered** at the top of the page (`.header { text-align: center }`
  in `cv_style.css`): name, contact line, and tagline all centered. Don't
  revert to flex right-aligned inadvertently.
- **Project section license**: the LLM is allowed to (a) reorder entries of a
  `reorderable: true` section, (b) remove an entry entirely if it doesn't add
  to the job application, (c) edit bullets. NEVER invent entries: every entry
  in the output must exist in the base CV (validated by `_validate_shape` by
  matching `heading`).
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

## Base CV format (`input/base_cv.yaml`)

Any user with a CV in PDF/Word can generate `input/base_cv.yaml` by:
  (a) asking their coding agent (Claude Code / Opencode) — see the skill in
      `.claude/skills/cv_automatizacion.md` for the exact procedure (read
      schema + example → map sections → write YAML → validate with
      `scripts/validate_base_cv.py` → autocorrect in a loop); or
  (b) pasting `PROMPT_PARA_TU_CV.md` into any AI chat.
The visual layout of the original CV is NOT preserved — output always uses the
repo's single Harvard template (intentional consistency guarantee).

Section equivalence table (for the PDF→YAML conversion task):
Experiencia / Educación / Proyectos / Certificaciones / Publicaciones /
Voluntariado → `entry_block`; Habilidades / Idiomas / Herramientas / Premios →
`simple_list`; Resumen / Perfil / Objetivo → `text_block`.

## Architecture map

- `schema/base_cv.schema.json` — JSON Schema contract for `input/base_cv.yaml`
  (3 section `type`s, `reorderable`, `links`, bullet `tags`).
- `schema/example.yaml` — complete example CV covering all 3 types.
- `src/config.py` — `Settings` frozen dataclass loaded from `.env`. LLM fields
  are provider-agnostic (`LLM_*`); defaults keep OpenCode Go + DeepSeek V4 Flash.
  Also `PREFERENCES_PATH` (user personal LLM instructions) and
  `ENABLE_EVALUATION` (skip evaluate+repair when false).
- `src/profile/schema_validation.py` — jsonschema validation with readable,
  actionable error messages (shared by the standalone script and `read_cv`).
- `src/profile/cv_reader.py` — reads `input/base_cv.yaml` (pyyaml + schema
  validation) into a `CVProfile` of generic `CVSection`s. Section `type` is
  `entry_block` / `simple_list` / `text_block`; every `links` array is captured
  as protected `Enlace` objects. Invalid files raise an explicit, actionable
  exception — never silently dropped.
- `src/profile/preferences.py` — `load_user_preferences()` reads the optional
  plain-text `input/preferences.txt` (comments/blank lines ignored).
- `src/tailor/prompt_loader.py` — `load_prompt(name)` returns
  `prompts/{name}.override.txt` if it exists, else `prompts/{name}.txt`
  (gitignored override → user customizations survive `git pull`).
- `prompts/` — plain-text system prompts: `tailor_system.txt`,
  `evaluator_system.txt`, `repair_system.txt`, `job_summarizer_system.txt`.
  Editable by any user without touching Python.
- `src/tailor/prompts.py` — prompt builders. System prompts come from
  `prompts/` via `load_prompt`; the dynamic "user" message carries the base CV
  view (URLs stripped), the job summary, and optionally the candidate's
  personal-preferences block. `JobInfo` carries the cached `JobSummary`.
- `src/tailor/job_summarizer.py` — first LLM pass per offer; reduces the raw
  job description to a structured `JobSummary` (`requisitos_duros` /
  `skills_deseadas` / `funciones_clave`) cached as `job_summary.json`. Its
  system prompt explicitly treats the job text as untrusted DATA, never as
  instructions (prompt-injection mitigation). Only the summary travels onward.
- `src/tailor/llm_client.py` — wrapper over the official `openai` SDK. Logs
  per-pass token usage (`completion.usage`) with a `tag` label so users can see
  where tokens are spent.
- `src/tailor/cv_rewriter.py` — tailor pass. After the LLM returns, calls
  `_reinject_links` to restore URLs from the base CV (matching by `heading`).
  `_validate_shape` is type/reorderable-aware and **deterministically drops**
  empty bullets (a lone "-" / "•") and empty-headed entries WITHOUT any LLM.
- `src/extract/mcp_stdio.py` — minimal JSON-RPC 2.0 over stdio. Raises the
  asyncio StreamReader buffer limit to 64 MiB (default 64 KiB) so that
  Playwright MCP `browser_snapshot` responses describing large LinkedIn
  pages don't crash with `Separator is found, but chunk is longer than
  limit`. Set via `limit=` on `asyncio.create_subprocess_exec`.
- `src/extract/linkedin_scraper.py` — uses Playwright MCP (default) or
  Browser MCP (`--scraper browsermcp`). Uses `browser_wait_for` with
  Spanish/English job-description markers to auto-wait for content, then
  `_try_click_see_more` to expand long descriptions. Persistent
  `--user-data-dir` via `PLAYWRIGHT_USER_DATA_DIR`.
- `src/render/html_renderer.py` — Jinja2 render of `templates/cv_template.html`
  + reused `templates/cv_style.css`. Generic over section `type` (text_block /
  simple_list / entry_block); the `entry_links_html` filter renders any entry's
  protected `links`; `_build_contact_html` builds the header from
  `personal_info`. Rewritable blocks carry `contenteditable="true"` +
  `data-field="..."`. Copies the CSS next to cv.html.
- `src/render/pdf_renderer.py` — Playwright Chromium headless render of
  `cv.html` → `cv.pdf` via `page.pdf(format="Letter", print_background=True)`.
  Atomic: emits `PdfResult{success, pdf_path, error}`.
- `src/render/legacy/docx_writer.py` + `pdf_converter.py` — legacy docx +
  LibreOffice path behind `--legacy-docx`. Requires a `base_cv.docx` template
  (does NOT work with the YAML base CV); backwards compat only.
- `src/review/server.py` — FastAPI app for `python run.py review <slug>`.
  `GET /` serves `cv.html`, `POST /save` overwrites it + kicks off PDF regen
  in a background thread. The save button is hidden in `@media print`.
- `run.py` — CLI. Subcommands: `all`, `extract`, `tailor`, `review`, `login`,
  `list`. Flags: `--new`, `--force`, `--job <url>`, positional `<url>` (alias
  for `--job`), `--dry-run`, `--limit N`, `--yes`, `--scraper playwright|browsermcp`,
  `--legacy-docx`. Loads user preferences once per run and passes them to the
  three prompt builders. When `settings.enable_evaluation` is false it skips
  evaluate/repair entirely. Prints each generated `cv.pdf` path + a reminder
  that `review <slug>` is available.

## analysis.json schema (the contract)

```
{
  "summary": "<rewritable one-liner / paragraph>",
  "sections": [
    {
      "id": "<immutable>",
      "title": "<immutable>",
      "type": "entry_block" | "simple_list" | "text_block",
      "reorderable": true|false,
      "entries": [                                   # entry_block only
        {
          "heading": "<immutable>", "subheading": "<immutable>",
          "location": "<immutable>", "dates": "<immutable>",
          "links": [{"label": "...", "url": "..."}],  # protected
          "bullets": [{"text": "...", "tags": [...]}] # rewritable
        }
      ],
      "items": [{"text": "...", "tags": [...]}],     # simple_list only
      "text": "..."                                  # text_block only
    }
  ]
}
```

The LLM **never sees** `links`. After tailor / repair, the orchestrator
re-injects URLs from the base CV. `_validate_shape` (called BEFORE reinjection)
flags any `links`/`enlaces` array the LLM might emit as a "url_tampered"
warning.

### Section-specific rules (driven by `type` + `reorderable`)

- **`entry_block` + `reorderable: true`**: entries may be REORDERED and REMOVED
  (if irrelevant to the job); bullet lists are editable. Never invent entries —
  every `heading` must exist in the base CV (validation flags this).
- **`entry_block` + `reorderable: false`**: strict 1:1 — same entries, same
  order, same bullet count per entry (± split/merge tolerance). `heading`,
  `subheading`, `location`, `dates` are immutable; only bullets are rewritable.
- **`simple_list`**: reorderable/reformulable items (skills, languages...).
- **`text_block`**: freely rewritable while respecting the facts.
- Deterministic (no-LLM) rule: any bullet whose `text` is empty or a lone
  separator after `strip()` is dropped, and any entry whose `heading` is empty
  is dropped — with a shape warning (see `_clean_empty_content`).

## When edits are needed

- If you add a new LLM pass, route it through `llm_client.LLMClient.chat` and
  load its system prompt from `prompts/<name>.txt` via
  `src.tailor.prompt_loader.load_prompt`.
- If you change the tailored JSON schema, also touch `_validate_shape` in
  `cv_rewriter.py`, the OUTPUT FORMAT block in `prompts/*.txt`, and
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
- The user's CV (`input/base_cv.yaml`), personal preferences
  (`input/preferences.txt`), and prompt overrides (`prompts/*.override.txt`)
  are gitignored.

## LLM provider (current)

- `.env` points at `LLM_BASE_URL=https://opencode.ai/zen/go/v1` (OpenCode Go
  subscription tier) with `LLM_MODEL_TAILOR=deepseek-v4-flash` and
  `LLM_MODEL_EVALUATOR=deepseek-v4-flash`. The `/go/` endpoint is rate-limited
  per workspace per month; the general `/zen/v1` endpoint is pay-as-you-go per
  token but currently unused.
- Any OpenAI-compatible provider works (DeepSeek direct, OpenRouter, etc.) —
  just edit `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL_*` in `.env`. No
  code change needed.

## Pipeline state (local machine)

- LinkedIn session is persisted in `.playwright-profile/` (headed login done
  once via `python run.py login`). Subsequent runs are headless and the
  scraper reuses the cookies.
- `jobs/` holds the user's cached LinkedIn jobs. Each job's JSON has a
  `tailored: true|false` flag and the `tailor --new` flag uses it to skip
  already-processed jobs.
- `output/` holds generated CVs, nested `output/<date>/<slug>/`. The
  `--force` flag re-tailors from scratch with the current LLM.
- The user's base CV lives in `input/base_cv.yaml` (converted from the old
  HTML; the old HTML/Word files remain in `input/` as historical references
  and are gitignored). The summary "En búsqueda de un puesto en..." rule lives
  in the user's `input/preferences.txt`, NOT in any generic prompt.

## Skills (for Claude Desktop / Opencode Desktop / Kimi Desktop)

- `.claude/skills/cv_automatizacion.md` — auto-loaded by Claude Desktop and
  Claude Code when opened in the project root. The skill teaches the
  assistant to translate natural-language requests like "generá el CV para
  la oferta `<url>`" into `python run.py all --job <url> --force`, and to
  handle "convertime mi CV a base_cv.yaml" / "generá mi CV base a partir de
  este PDF" as a supported task.
- `.opencode/command/cv_automatizacion.md` — same content, exposed as the
  `/cv_automatizacion` command in Opencode Desktop / CLI.
- For Kimi Desktop (no auto-skill loader): paste the file contents into the
  custom instructions / system prompt manually.

## Test status

`pytest tests/ -v` → 205 passed, 0 failed (verified after every prompt /
renderer / scraper edit). Don't break this.
