#!/usr/bin/env bash
# run.sh — one-shot entrypoint for the auto-tailored-cv pipeline.
#
# Ensures the environment (venv + pinned deps + Playwright Chromium + npx on
# PATH) is ready, then runs `run.py` with the same arguments.
#
# Usage (identical to run.py):
#   ./run.sh all <url> --force
#   ./run.sh tailor --last 1 --dry-run
#   ./run.sh review <job_slug>
#   ./run.sh --help
#
# This is the recommended way to invoke the pipeline from an agent/LLM:
# it re-bootstraps automatically only when the venv is missing or the pinned
# playwright version drifts, and it makes `npx` reachable (node/npx live in
# /usr/local/bin, which is not always on the agent shell's PATH).

set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python"

needs_bootstrap=0
if [ ! -x "$VENV_PY" ]; then
  needs_bootstrap=1
elif ! "$VENV_PY" -c "import rich, openai, playwright" >/dev/null 2>&1; then
  needs_bootstrap=1
elif [ "$("$VENV_PY" -c "from importlib.metadata import version; print(version('playwright'))" 2>/dev/null || echo x)" != "1.60.0" ]; then
  needs_bootstrap=1
fi

if [ "$needs_bootstrap" = "1" ]; then
  echo ">> environment needs setup; running scripts/bootstrap.sh"
  bash "$REPO_ROOT/scripts/bootstrap.sh"
fi

exec "$VENV_PY" run.py "$@"
