# auto-tailored-cv

> Automatically tailor your résumé for each job you saved on LinkedIn — output `.html` + `.pdf` — without making it look like an obvious keyword-stuffed rewrite.

`README in English | [Leer en español](README.md)`

---

## ⚠️ Disclaimer

This tool automates interaction with LinkedIn using a logged-in browser session via [Playwright MCP](https://github.com/microsoft/playwright-mcp). **LinkedIn's User Agreement prohibits automated access** to its platform. Using this tool can lead to temporary or permanent restrictions on your LinkedIn account. Use at your own risk. The authors of this project are not responsible for any consequences, including account suspension.

The project does **not** store, transmit, or sell your LinkedIn credentials. All scraping happens locally on your machine via a persistent Chromium profile.

---

## What it does

Given your base CV in YAML format (`input/base_cv.yaml`, validated against
`schema/base_cv.schema.json`), the system:

1. **Extracts** — connects to your LinkedIn session via Playwright MCP (with `--user-data-dir` persistence so your login survives across runs), navigates to your saved jobs page, and pulls each saved job's title, company, location, and full description. The scraper clicks the "...más" / "See more" button on long job descriptions so the full text is captured. You can also pass any LinkedIn job URL with `--job <url>`.
2. **Summarizes the job** — an LLM pass reduces the raw description to a small structured summary (`requisitos_duros` / `skills_deseadas` / `funciones_clave`), cached once per offer. Only that summary travels to the later passes (big token saving). Because this is the only pass that processes the raw third-party job text, its system prompt explicitly treats it as untrusted DATA, never as instructions (prompt-injection mitigation).
3. **Profiles** — reads `input/base_cv.yaml` (pyyaml + jsonschema) and structures it into **generic** sections: `entry_block`, `simple_list`, or `text_block`. Section names are just titles — any combination/order renders consistently. Hyperlinks are extracted as protected `{label, url}` objects — URLs **never** reach the LLM.
4. **Tailors** — calls an LLM to rewrite the CV so it aligns naturally with each job's requirements. The prompt forbids inventing facts, copying phrases verbatim, keyword stuffing, changing dates/roles, or touching URLs. Behavior per section is driven by `type` + `reorderable`: `reorderable: true` sections may be reordered/trimmed; `reorderable: false` sections are strict 1:1. The `text_block` profile is rewritten per offer; Experience/Project bullets are paraphrased and reordered by relevance; skills are prioritized (most relevant first) with individual irrelevant items omittable — all source categories are always preserved.
5. **Evaluates** — a second LLM pass reviews the tailored CV against the job summary and your base CV, flagging hallucinations, incongruities, format issues, and forced alignment (configurable with `ENABLE_EVALUATION=false`).
6. **Repairs** — if the evaluator found issues, a third LLM pass fixes only the flagged problems.
7. **Renders HTML** — Jinja2 renders `cv.html` from `templates/cv_template.html`, reusing `templates/cv_style.css`. The header (name, contact line, tagline) is **centered**. Each rewritable block carries `contenteditable="true"` + a unique `data-field`.
8. **Renders PDF** — Playwright Chromium headless converts `cv.html` → `cv.pdf` via `page.pdf(format="Letter", print_background=True)`.
9. **Optional review** — `python run.py review <job_slug>` serves `cv.html` locally; edits hit `/save` and regenerate `cv.pdf` live.

The result for each saved job is a folder (nested by date):

```
output/
└── 2026-07-23/
    └── senior-data-engineer_acme/
        ├── cv.html                        # editable Jinja2 render
        ├── cv.pdf                         # Playwright PDF
        ├── cv_style.css                   # copied next to cv.html (self-contained)
        ├── job_description.txt            # raw extraction
        ├── analysis.json                  # tailored CV (structured, urls protected)
        └── evaluation.json                # evaluator verdict + any repairs
```

The pipeline is **incremental**: re-running only processes newly-saved jobs. Already-processed jobs are skipped unless you pass `--force`. The `--job <url>` flag accepts **any** LinkedIn job URL (saved or not, from search or from your saved-jobs page) — the scraper navigates directly to that one posting instead of listing your saved jobs.

---

## Architecture

```
extract  →  summarize_job  →  tailor  →  evaluate  →  repair  →  render_html  →  [review opcional]  →  render_pdf
(Playwright MCP)  (LLM, once/offer)  (LLM)                (optional)         (Jinja2)        (Playwright Chromium)
```

Up to four LLM passes per offer — *summarize_job* (once, cached), *tailor*
(rewrite), *evaluate* (review), and *repair* (fix) — use the configurable
models. The system prompts are plain-text files under `prompts/` that you can
read and edit without touching Python (see below).

### Hyperlink / URL protection

Every link in the base CV is extracted into a structured `{label, url}` object.
The prompt builders omit URLs entirely — the LLM only sees visible text. After
the tailor returns, the original `links` arrays are re-injected byte-identical,
so the user's project links survive the rewrite untouched (and a buggy LLM
cannot tamper with them).

### Editable system prompts (`prompts/`)

`prompts/tailor_system.txt`, `prompts/evaluator_system.txt`,
`prompts/repair_system.txt`, and `prompts/job_summarizer_system.txt` are plain
text. To customize one, create `prompts/<name>.override.txt` (gitignored): it
wins over the default and survives `git pull` without merge conflicts.

### Personal preferences (`input/preferences.txt`, optional)

Write your own rules (e.g. *"the summary must start with 'En búsqueda de un
puesto en...'"*) in `input/preferences.txt` (plain text; `#` lines ignored; see
`input/preferences.example.txt`). They're injected into all three LLM passes as
"INSTRUCCIONES PERSONALES DEL CANDIDATO", always subordinate to the critical
rules (no invented data, no verbatim copying).

---

## Project layout

```
auto-tailored-cv/
├── .github/workflows/ci.yml
├── .gitignore
├── .env.example                # template for your secrets (committed)
├── .env                         # your actual secrets (NEVER committed)
├── schema/
│   ├── base_cv.schema.json      # JSON Schema contract for input/base_cv.yaml
│   └── example.yaml             # complete example CV (reference for conversion)
├── prompts/                     # editable system prompts (.txt)
├── PROMPT_PARA_TU_CV.md        # copy-paste prompt to convert your CV in any AI chat
├── .claude/skills/cv_automatizacion.md    # natural-language skill
├── .claude/skills/editar_cv.md             # edit-your-base-CV skill
├── .opencode/command/cv_automatizacion.md # opencode command equivalent
├── .opencode/command/editar_cv.md         # /editar-cv command equivalent
├── README.md                   # Spanish docs
├── README.en.md                # this file (English)
├── LICENSE                     # MIT
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt        # + pytest, ruff, python-docx (legacy --legacy-docx)
├── scripts/
│   ├── build_base_cv.py         # generates a placeholder input/base_cv.yaml
│   ├── validate_base_cv.py      # standalone YAML validator (schema)
│   ├── install_libreoffice.sh
│   └── install_skill.sh         # install all skills globally
├── run.py                       # CLI entrypoint
├── input/
│   ├── base_cv.yaml             # your base CV (YAML, gitignored)
│   ├── preferences.example.txt  # template for personal preferences (committed)
│   └── preferences.txt          # your personal preferences (gitignored)
├── jobs/                        # extracted job JSONs (gitignored, cache)
├── output/                      # generated CVs (gitignored)
├── templates/
│   ├── cv_template.html         # generic Jinja2 template (entry_block/simple_list/text_block)
│   └── cv_style.css             # shared styles (Harvard, @media print)
├── src/
│   ├── config.py                # Settings (LLM_*, scraper, paths, review server)
│   ├── extract/
│   │   ├── linkedin_scraper.py  # Playwright MCP (default) / Browser MCP (legacy)
│   │   └── mcp_stdio.py         # generic JSON-RPC 2.0 stdio client
│   ├── profile/
│   │   ├── cv_reader.py         # YAML parser + CVProfile / CVSection / CVEntry
│   │   ├── schema_validation.py # jsonschema validation (readable errors)
│   │   └── preferences.py       # load_user_preferences()
│   ├── tailor/
│   │   ├── llm_client.py        # OpenAI SDK wrapper + token-usage logging
│   │   ├── prompt_loader.py     # load_prompt() with .override.txt support
│   │   ├── prompts.py           # prompt builders (system from prompts/, user dynamic)
│   │   ├── job_summarizer.py    # summarize_job() (cached once per offer)
│   │   ├── cv_rewriter.py       # tailor pass + link re-injection + shape validation
│   │   ├── evaluator.py
│   │   └── repair.py
│   ├── render/
│   │   ├── html_renderer.py     # Jinja2 → cv.html (generic types)
│   │   ├── pdf_renderer.py      # Playwright → cv.pdf
│   │   └── legacy/              # behind --legacy-docx only
│   │       ├── docx_writer.py
│   │       └── pdf_converter.py
│   ├── review/
│   │   └── server.py            # FastAPI local review server
│   └── utils/
│       ├── slugify.py
│       └── logging.py
└── tests/
    ├── test_cv_reader.py
    ├── test_schema_validation.py
    ├── test_preferences.py
    ├── test_prompt_loader.py
    ├── test_job_summarizer.py
    ├── test_html_renderer.py
    ├── test_pdf_renderer.py
    ├── test_tailor_pipeline.py
    ├── test_run_cli.py
    ├── test_linkedin_scraper.py
    ├── test_mcp_stdio.py
    ├── test_slugify.py
    ├── test_helpers_llm.py
    └── legacy/                  # legacy --legacy-docx tests
        ├── test_docx_writer.py
        └── test_pdf_converter.py
```

---

## Installation

### Prerequisites

- macOS (tested on macOS 14+; Linux should work with minor adjustments)
- Python 3.9+
- An LLM provider with an OpenAI-compatible endpoint (default: OpenCode Go subscription at `https://opencode.ai/zen/go/v1` with DeepSeek V4 Flash as the model; get an API key at <https://opencode.ai/auth>). Any alternative — OpenRouter, DeepSeek direct, … — works by setting `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_TAILOR`, `LLM_MODEL_EVALUATOR` in `.env`.
- Playwright Chromium — installed automatically by:
  ```bash
  python3 -m playwright install chromium
  ```
- Playwright MCP (for the scrape step) — auto-launched by the pipeline. You'll be logged into LinkedIn once on the first run, and your session persists in `.playwright-profile/` afterwards.
- LibreOffice is **optional** (only needed if you pass `--legacy-docx`); the main pipeline uses Playwright to render PDFs.

### Setup

```bash
git clone https://github.com/<your-user>/auto-tailored-cv.git
cd auto-tailored-cv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # for tests + legacy docx path
python3 -m playwright install chromium

cp .env.example .env
# open .env and paste your LLM provider API key (LLM_API_KEY)

# Generate your base CV in YAML. Two paths:
#   (a) ask your coding agent: "generá mi CV base a partir de este PDF" — it
#       reads schema/ + example and writes input/base_cv.yaml for you.
#   (b) or paste PROMPT_PARA_TU_CV.md into any AI chat with your CV and save
#       the returned YAML at input/base_cv.yaml.
# Then validate:
python scripts/validate_base_cv.py input/base_cv.yaml
```

> ⚠️ The **visual layout** of your original CV (columns, icons, photo, colors)
> is **not** preserved — only the content. The final PDF always uses the repo's
> single Harvard template. This is intentional: it guarantees a consistent look
> across users.

To use the `--scraper browsermcp` fallback instead of Playwright MCP, install the [Browser MCP Chrome extension](https://docs.browsermcp.io) (kept only for transition).

---

## Usage

```bash
# Full pipeline (extract all saved jobs, tailor each, render HTML + PDF)
python run.py all

# Only scrape LinkedIn → jobs/*.json
python run.py extract

# Only tailor already-extracted jobs
python run.py tailor

# Only process jobs saved since the last run (incremental)
python run.py all --new

# Scrape + tailor ONE specific job URL (saved or not — any LinkedIn job URL works)
python run.py all --job https://www.linkedin.com/jobs/view/4431977634/ --force

# Limit to the first N jobs
python run.py all --limit 1

# Dry run: show what would be processed without calling the LLM
python run.py all --dry-run

# List all generated CVs (job_slugs for `review`)
python run.py list

# Edit a tailored CV in your browser + regenerate PDF
python run.py review 2026-07-23/senior-data-engineer_acme
# (bare sluges also work: python run.py review senior-data-engineer_acme)

# Switch the scraper backend temporarily
python run.py all --scraper browsermcp     # legacy fallback
python run.py all --scraper playwright     # default

# Use the legacy docx + LibreOffice render path (instead of HTML + Playwright)
python run.py all --legacy-docx

# Log into LinkedIn once (opens a visible Chromium window)
python run.py login
```

Outputs land in `output/<date>/<slug>_<company>/`. At the end of a run, `python run.py all` prints the path of each generated `cv.pdf` plus a reminder that `review <slug>` is available if any result didn't convince you.

### Running from a desktop assistant (Claude Desktop / Opencode Desktop / Kimi Desktop)

If you'd rather not use the terminal, you can ask a desktop assistant that
supports skills / custom commands to drive the pipeline for you. The repo
ships with skill files and a one-command installer:

```bash
# Install the skill globally so it works from any directory
./scripts/install_skill.sh

# Uninstall later
./scripts/install_skill.sh --uninstall
```

This copies the skills to:
- `~/.claude/skills/cv_automatizacion.md` and `~/.claude/skills/editar_cv.md` —
  auto-loaded by **Claude Code** and **Claude Desktop** in any project.
- `~/.config/opencode/skills/cv_automatizacion/SKILL.md` and
  `~/.config/opencode/skills/editar_cv/SKILL.md` — auto-loaded by
  **Opencode** (CLI or Desktop) in any project.

The repo also keeps project-local copies:
- `.claude/skills/cv_automatizacion.md` and `.claude/skills/editar_cv.md` —
  auto-loaded by Claude Code when you open it in this repo (no install needed).
- `.opencode/command/cv_automatizacion.md` and
  `.opencode/command/editar_cv.md` — exposed as the `/cv_automatizacion` and
  `/editar-cv` commands in Opencode when you open it in this repo.

For **Kimi Desktop** (no skill auto-loader), paste the contents of
`.claude/skills/cv_automatizacion.md` into your custom instructions or
system prompt manually.

Once installed, just say in natural language: *"Generá el CV para la oferta
https://www.linkedin.com/jobs/view/123/"* and the assistant runs
`python run.py all --job <url> --force` for you. The skill file teaches the
assistant to translate natural-language requests ("solo las nuevas",
"regenerar este: <url>", "revisar el de <job_slug>"…) into the right CLI
flags. The output and behavior is identical to running the CLI by hand.
To change your underlying data instead (add a skill, a job, a project, edit
bullets…), say *"editá mi CV base"* — the `editar_cv` skill (`/editar-cv`)
edits `input/base_cv.yaml` and validates it automatically.

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `LLM_API_KEY` | — | Your LLM provider API key (required). Any OpenAI-compatible endpoint works |
| `LLM_BASE_URL` | `https://opencode.ai/zen/go/v1` | OpenAI-compatible endpoint (OpenCode Go, DeepSeek, OpenRouter, …) |
| `LLM_MODEL_TAILOR` | `deepseek-v4-flash` | Model for the rewrite pass |
| `LLM_MODEL_EVALUATOR` | `deepseek-v4-flash` | Model for the evaluator + repair pass |
| `LLM_REQUEST_TIMEOUT` | `120` | Per-request timeout (seconds) |
| `SCRAPER_BACKEND` | `playwright` | `playwright` (default) or `browsermcp` (legacy) |
| `PLAYWRIGHT_MCP_COMMAND` | `npx` | NPM command that launches Playwright MCP |
| `PLAYWRIGHT_MCP_ARGS` | `-y @playwright/mcp@latest` | Args for Playwright MCP |
| `PLAYWRIGHT_USER_DATA_DIR` | `.playwright-profile` | Persisted Chromium profile (LinkedIn cookies). Gitignored — contains secrets |
| `BROWSER_MCP_COMMAND` | `npx` | (legacy) NPM command that launches Browser MCP |
| `BROWSER_MCP_ARGS` | `-y @browsermcp/mcp@latest` | (legacy) Args for Browser MCP |
| `LINKEDIN_SAVED_JOBS_URL` | `https://www.linkedin.com/my-items/saved-jobs/` | Saved jobs page URL |
| `BROWSER_TIMEOUT_MS` | `15000` | Per-action timeout (ms) |
| `BROWSER_NAV_DELAY_S` | `3` | Fallback nav sleep (lapsus before snapshot). With Playwright MCP's auto-wait this is largely redundant |
| `BASE_CV_PATH` | `input/base_cv.yaml` | Path to your base CV (YAML) |
| `PREFERENCES_PATH` | `input/preferences.txt` | Optional personal preferences for the LLM |
| `ENABLE_EVALUATION` | `true` | When `false`, skips the evaluator + repair passes (cheaper, but nothing catches hallucinations / verbatim copying) |
| `JOBS_DIR` | `jobs` | Cache directory for extracted jobs |
| `OUTPUT_DIR` | `output` | Where tailored CVs are written |
| `TEMPLATES_DIR` | `templates` | Jinja templates + shared CSS |
| `SOFFICE_PATH` | `soffice` | LibreOffice binary (only used by `--legacy-docx`) |
| `REVIEW_HOST` | `localhost` | host for `run.py review` |
| `REVIEW_PORT` | `8420` | port for `run.py review` |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Copy `.env.example` to `.env` and fill in your values. The defaults keep pointing at OpenCode Go + DeepSeek V4 Flash; to switch providers, change only `LLM_BASE_URL` and `LLM_API_KEY`.

---

## Safety / privacy

- Your `.env`, `input/base_cv.yaml`, `input/preferences.txt`, and `.playwright-profile/` are gitignored — CV and cookies never leave your machine via git.
- LinkedIn scraping uses your real Chromium session via Playwright MCP — no password is stored in this project.
- URLs in the base CV are protected end-to-end: they never appear in LLM prompts and are re-injected byte-identical after the tailor pass.
- Job-posting text from LinkedIn is treated as **untrusted data**: the only pass that processes it raw (the job summarizer) explicitly declares it is never an instruction to follow (prompt-injection mitigation).
- All LLM prompts go to your configured provider endpoint with whatever data-retention policy that provider has (OpenCode Go by default has zero data retention).

---

## Contributing

This is a personal project but PRs are welcome — please open an issue first to discuss what you'd like to change.

---

## License

MIT — see [`LICENSE`](LICENSE).