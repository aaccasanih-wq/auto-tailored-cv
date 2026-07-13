# auto-tailored-cv

> Automatically tailor your résumé for each job you saved on LinkedIn — and output both `.docx` and `.pdf` — without making it look like an obvious keyword-stuffed rewrite.

`README in English | [Leer en español](README.es.md)`

---

## ⚠️ Disclaimer

This tool automates interaction with LinkedIn using your own logged-in browser session via [Browser MCP](https://github.com/browsermcp/mcp). **LinkedIn's User Agreement prohibits automated access** to its platform. Using this tool can lead to temporary or permanent restrictions on your LinkedIn account. Use at your own risk. The authors of this project are not responsible for any consequences, including account suspension.

The project does **not** store, transmit, or sell your LinkedIn credentials. All scraping happens locally on your machine via your own Chrome profile.

---

## What it does

Given a folder containing your base CV in Word format, the system:

1. **Extracts** — connects to your Chrome browser via Browser MCP, navigates to your LinkedIn saved jobs page, and pulls each saved job's title, company, location, requirements, and full description.
2. **Profiles** — reads your base `.docx` CV with `python-docx` and structures it into sections (experience, skills, education, etc.).
3. **Tailors** — calls an LLM (via OpenCode Go API, OpenAI-compatible) to rewrite the CV so it aligns naturally with each job's requirements. The prompt explicitly forbids:
   - inventing new skills or experiences you don't have,
   - copying phrases verbatim from the job posting,
   - keyword stuffing,
   - changing dates or roles.
4. **Evaluates** — a second LLM pass reviews the tailored CV against the job posting and your base CV, flagging hallucinations, incongruities, format issues, and any "forced" alignment.
5. **Repairs** — if the evaluator found issues, a third LLM pass fixes only the flagged problems.
6. **Renders** — `python-docx` rebuilds a `.docx` (preserving the layout of your base CV). LibreOffice headless converts `.docx` → `.pdf`.

The result for each saved job is a folder:

```
output/2026-07-13_senior-data-engineer_acme/
├── cv.docx
├── cv.pdf
├── job_description.txt          # raw extraction
├── analysis.json                # tailored CV (structured)
└── evaluation.json              # evaluator verdict + any repairs
```

The pipeline is **incremental**: re-running only processes newly-saved jobs. Already-processed jobs are skipped unless you pass `--force`.

---

## Architecture

```
extract  →  profile  →  tailor  →  evaluate  →  repair  →  render
(BrowserMCP)        (python-docx)            (OpenCode Go, glm-5.2)        (docx + soffice)
```

Two LLM passes — *tailor* (rewrite) and *evaluate* (review) — both use **GLM 5.2** via [OpenCode Go](https://opencode.ai/docs/go/), a low-cost subscription that includes this model. See [`docs/PROMPT_DESIGN.md`](docs/PROMPT_DESIGN.md) for the anti-suspicion prompt strategy.

---

## Project layout

```
auto-tailored-cv/
├── .github/workflows/ci.yml
├── .gitignore
├── .env.example                # template for your secrets (committed)
├── .env                        # your actual secrets (NEVER committed)
├── README.md                   # this file (English)
├── README.es.md                # Spanish version
├── LICENSE                     # MIT
├── pyproject.toml
├── requirements.txt
├── run.py                      # CLI entrypoint
├── input/
│   └── base_cv.docx            # your base CV (gitignored)
├── jobs/                       # extracted job JSONs (gitignored, cache)
├── output/                     # generated CVs (gitignored)
├── src/
│   ├── config.py
│   ├── extract/linkedin_scraper.py
│   ├── profile/cv_reader.py
│   ├── tailor/
│   │   ├── llm_client.py
│   │   ├── prompts.py
│   │   ├── cv_rewriter.py
│   │   ├── evaluator.py
│   │   └── repair.py
│   ├── render/
│   │   ├── docx_writer.py
│   │   └── pdf_converter.py
│   └── utils/
│       ├── slugify.py
│       └── logging.py
└── tests/
```

---

## Installation

### Prerequisites

- macOS (tested on macOS 14+; Linux should work with minor adjustments)
- Python 3.9+
- Google Chrome, logged into your LinkedIn account
- [Browser MCP Chrome extension](https://docs.browsermcp.io) installed
- An OpenCode Go subscription (for the LLM) — get an API key at <https://opencode.ai/auth>
- LibreOffice (for `.docx` → `.pdf`; the install script downloads it for you)

### Setup

```bash
git clone https://github.com/<your-user>/auto-tailored-cv.git
cd auto-tailored-cv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# open .env and paste your OpenCode API key

# put your base CV here
cp /path/to/your/cv.docx input/base_cv.docx

# install LibreOffice (skips if present)
./scripts/install_libreoffice.sh
```

For Browser MCP, make sure the Chrome extension is installed and enabled, and that the local MCP server is registered for your opencode config (see `.env.example`).

---

## Usage

```bash
# Full pipeline (extract all saved jobs, tailor each, render)
python run.py all

# Only scrape LinkedIn → jobs/*.json
python run.py extract

# Only tailor already-extracted jobs
python run.py tailor

# Only process jobs saved since the last run (incremental)
python run.py all --new

# Re-process one specific job URL, ignoring cache
python run.py all --job https://www.linkedin.com/jobs/view/<id> --force

# Dry run: show what would be processed without calling the LLM
python run.py all --dry-run
```

Outputs land in `output/<date>_<slug>_<company>/`.

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `OPENCODE_API_KEY` | — | Your OpenCode API key (required) |
| `OPENCODE_BASE_URL` | `https://opencode.ai/zen/go/v1` | OpenCode Go endpoint |
| `OPENCODE_MODEL_TAILOR` | `glm-5.2` | Model for the rewrite pass |
| `OPENCODE_MODEL_EVALUATOR` | `glm-5.2` | Model for the evaluator pass |
| `LINKEDIN_SAVED_JOBS_URL` | `https://www.linkedin.com/my-items/saved-jobs/` | Saved jobs page URL |
| `BASE_CV_PATH` | `input/base_cv.docx` | Path to your base CV |
| `JOBS_DIR` | `jobs` | Cache directory for extracted jobs |
| `OUTPUT_DIR` | `output` | Where tailored CVs are written |
| `SOFFICE_PATH` | `soffice` | LibreOffice binary |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Copy `.env.example` to `.env` and fill in your values.

---

## Safety / privacy

- Your `.env` and `input/*.docx` are gitignored — your CV and keys never leave your machine via git.
- LinkedIn scraping uses your real Chrome session via Browser MCP — no password is stored anywhere in this project.
- All LLM prompts go through OpenCode Go with a zero data-retention policy.

---

## Contributing

This is a personal project but PRs are welcome — please open an issue first to discuss what you'd like to change.

---

## License

MIT — see [`LICENSE`](LICENSE).