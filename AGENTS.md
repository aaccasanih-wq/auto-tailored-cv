# AGENTS.md — guide for opencode/AI agents working on this repo

## What this project is

`auto-tailored-cv` reads a base CV in `.docx` and a list of saved LinkedIn jobs,
then rewrites the CV naturally for each job using an LLM (OpenCode Go API —
GLM 5.2), and outputs both `.docx` and `.pdf`. The pipeline is:

```
extract → profile → tailor → evaluate → repair → render
```

## Commands you should run before declaring a task done

- `pytest tests/ -v` — must be 100% passing.
- `python3 run.py --help` — smoke check the CLI doesn't crash.
- `python3 run.py all --dry-run` — runs the cache logic without LLM calls
  (will report "no saved jobs" if BrowserMCP isn't connected, which is fine).

## Hard rules

- **Never commit `.env`.** The `.gitignore` covers it, but double-check before
  every commit. Any real `OPENCODE_API_KEY` value is a secret.
- **Never commit `input/*.docx`.** The user's CV is personal data.
- **Never commit `jobs/` or `output/`.** They are runtime, regenerable.
- Don't install `mcp` PyPI package — it requires Python 3.10+; on this machine
  we have Python 3.9. We use `src/extract/mcp_stdio.py` instead.
- Don't add `linkedin-mcp-server` (the stickerdaniel one) — it's out of scope.
  We talk to **Browser MCP** (the Chrome-extension one) directly.
- No new LLM client library. We use the official `openai` SDK pointed at
  OpenCode Go's OpenAI-compatible endpoint.

## Architecture map

- `src/config.py` — `Settings` frozen dataclass loaded from `.env` via dotenv.
- `src/profile/cv_reader.py` — reads a .docx into a structured `CVProfile`
  (name/contact/summary/sections[title,paragraphs,tables]).
- `src/tailor/` — three LLM passes (tailor / evaluator / repair), all using GLM 5.2.
- `src/tailor/llm_client.py` — wrapper over the official `openai` SDK.
- `src/tailor/prompts.py` — prompt builders. Anti-suspicion strategy is baked
  in here: no new facts, no verbatim copying, same shape as the base CV.
- `src/render/docx_writer.py` — opens base_cv.docx as a template and substitutes
  text run-by-run, preserving fonts/margins.
- `src/render/pdf_converter.py` — calls `soffice --headless --convert-to pdf`.
- `src/extract/mcp_stdio.py` — minimal JSON-RPC 2.0 client for MCP over stdio.
- `src/extract/linkedin_scraper.py` — uses BrowserMCP to scrape saved jobs.
- `run.py` — CLI entrypoint. Subcommands: `all`, `extract`, `tailor`. Flags:
  `--new`, `--force`, `--job <url>`, `--dry-run`, `--limit N`.

## When edits are needed

- If you add a new LLM pass, route it through `llm_client.LLMClient.chat`.
- If you change the tailored JSON schema, also touch `_validate_shape` in
  `cv_rewriter.py` and the docx_writer's substitution loop.
- Run `pytest tests/test_tailor_pipeline.py -v` after any prompt edit to make
  sure the stub-based tests still pass.
- If you need to debug BrowserMCP, run `npx @browsermcp/mcp@latest` directly
  in a terminal to see its stderr. Don't try to mock the MCP server in tests.

## Secrets handling

- Real values live ONLY in `.env` (gitignored).
- The committed template is `.env.example`.
- If you see a real key in any file you're about to commit, STOP and rewrite
  it to the placeholder before staging.