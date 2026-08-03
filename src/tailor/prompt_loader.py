"""Loader for the user-editable system prompts.

Prompts live as plain-text files in `prompts/` so any user — even with no
technical background — can read exactly what the LLM is asked at each stage and
edit the files directly (or ask their AI to improve them) without touching
Python code.

Override mechanism:
  - `prompts/{name}.txt`           → default prompt, versioned in the repo.
  - `prompts/{name}.override.txt`  → the user's local customization. If it
    exists it wins. It is gitignored, so `git pull` / updates never clobber a
    user's personal edits and there are no merge conflicts to manage.

Names in use: `tailor_system`, `evaluator_system`, `repair_system`,
`job_summarizer_system`.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(name: str) -> str:
    """Return `prompts/{name}.override.txt` if it exists, else
    `prompts/{name}.txt`. Raises FileNotFoundError if neither exists."""
    override = PROMPTS_DIR / f"{name}.override.txt"
    default = PROMPTS_DIR / f"{name}.txt"
    path = override if override.exists() else default
    if not path.exists():
        raise FileNotFoundError(
            f"no prompt file at {path} (looked for {name}.txt and "
            f"{name}.override.txt under {PROMPTS_DIR})"
        )
    return path.read_text(encoding="utf-8").strip()


__all__ = ["PROMPTS_DIR", "load_prompt"]
