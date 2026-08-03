"""Load the user's optional personal instructions to the LLM.

The file is a plain-text file (default `input/preferences.txt`, gitignored).
Blank lines and lines starting with '#' are ignored. The remaining lines are
joined with newlines and returned as one string. Returns `""` when the file
is absent or empty after filtering.

The returned string is injected into the dynamic "user" messages of the
tailor / evaluator / repair prompts (never mixed into the static
`prompts/*.txt` files, which are repo-wide rules).
"""

from __future__ import annotations

from pathlib import Path


def load_user_preferences(path: Path) -> str:
    """Read a plain-text preferences file. Blank lines and lines starting
    with '#' are ignored. Returns '' if the file is missing or ends up empty.
    """
    p = Path(path)
    if not p.exists():
        return ""
    lines = [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(lines).strip()


__all__ = ["load_user_preferences"]
