#!/usr/bin/env bash
# bootstrap.sh — create (or refresh) the local virtualenv so the pipeline
# runs with a single command. Idempotent: safe to re-run at any time.
#
# What it does:
#   1. Picks a Python >= 3.9 that can create a venv (prefers 3.11/3.13).
#   2. Creates `.venv` if missing (never re-creates an existing one).
#   3. Installs the pinned requirements (playwright==1.60.0 → chromium 1223,
#      the last build that supports macOS 12).
#   4. Downloads the matching Playwright Chromium if not already cached.
#   5. Verifies `npx` is reachable (needed by the Playwright MCP scraper).
#
# Usage:
#   bash scripts/bootstrap.sh
#
# After it succeeds, run the pipeline with:
#   .venv/bin/python run.py all <url> --force

set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo ">> bootstrap: $(date '+%H:%M:%S')"

# --- 1. Pick a Python interpreter ------------------------------------------
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "ERROR: no Python >= 3.9 found. Install Python 3.9+ and re-run." >&2
  exit 1
fi
echo ">> python: $PYTHON ($("$PYTHON" --version 2>&1))"

# --- 2. Create venv if missing ---------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo ">> creating .venv"
  "$PYTHON" -m venv .venv
fi
PY=".venv/bin/python"

# --- 3. Install pinned requirements ----------------------------------------
echo ">> installing requirements (idempotent)"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

# --- 4. Ensure Playwright Chromium (pinned version, macOS 12 compatible) ---
echo ">> ensuring Playwright Chromium"
if ! "$PY" -m playwright install chromium; then
  echo "WARN: playwright install chromium failed (older macOS?)" >&2
  echo "WARN: continuing; pdf generation may fail, html will still render." >&2
fi

# --- 5. Verify npx -----------------------------------------------------------
if command -v npx >/dev/null 2>&1; then
  echo ">> npx: ok ($(command -v npx))"
else
  echo "WARN: npx not found in PATH. LinkedIn scraping (extract) needs Node/npx." >&2
  echo "WARN: install Node.js or add /usr/local/bin to PATH." >&2
fi

echo ">> smoke test imports"
"$PY" -c "import rich, openai, playwright, yaml, jinja2; print('   deps OK')"

echo ">> bootstrap done. Run the pipeline with:"
echo "   .venv/bin/python run.py all <url> --force"
