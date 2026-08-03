#!/usr/bin/env python3
"""Validate an `input/base_cv.yaml` against `schema/base_cv.schema.json`.

Standalone: no LLM, no LinkedIn login, no network — only pyyaml + jsonschema.

Usage:
    python scripts/validate_base_cv.py input/base_cv.yaml

Exit code 0 = valid, 1 = invalid (errors printed), 2 = usage/io error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `src` importable when run as a standalone script from any cwd.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.profile.schema_validation import validate_yaml_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_base_cv.py",
        description="Valida input/base_cv.yaml contra schema/base_cv.schema.json",
    )
    parser.add_argument("path", nargs="?", default=str(_ROOT / "input" / "base_cv.yaml"),
                        help="ruta al YAML (default: input/base_cv.yaml)")
    args = parser.parse_args(argv)

    errors = validate_yaml_file(Path(args.path))
    if not errors:
        print(f"OK: {args.path} valida contra schema/base_cv.schema.json")
        return 0

    print(f"ERROR: {args.path} no valida contra schema/base_cv.schema.json")
    print("Errores:")
    for e in errors:
        print(f"  - {e}")
    print("\nCorrige el archivo y vuelve a correr este script hasta que pase.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
