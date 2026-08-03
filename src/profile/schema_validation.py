"""Schema validation for `input/base_cv.yaml` against `schema/base_cv.schema.json`.

Shared by:
  - `scripts/validate_base_cv.py` (standalone CLI, no LLM / LinkedIn needed)
  - `src/profile/cv_reader.py` (called by `read_cv()` before parsing)

Errors are formatted as human-readable, actionable strings such as
`sections[2].entries[1]: 'dates' is a required property` instead of a raw
jsonschema traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft7Validator
except ImportError:  # pragma: no cover
    jsonschema = None
    Draft7Validator = None

from src.config import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "schema" / "base_cv.schema.json"

# Typo-friendly synonyms mapping → canonical field names. Used only to enrich
# error messages (never to auto-correct data).
_CANONICAL = {
    "titulo": "heading",
    "fecha": "dates",
    "subtitulo": "subheading",
    "enlaces": "links",
    "bullet": "bullets",
    "kind": "type",
    "table": "items",
}


class BaseCvValidationError(ValueError):
    """Raised when a base CV does not validate against the schema."""


def load_schema() -> dict[str, Any]:
    return json_load(SCHEMA_PATH)


def json_load(path: Path) -> dict[str, Any]:
    import json
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _clean_path(json_path: str) -> str:
    """Convert a jsonschema json_path (`$.sections[2].entries[1].dates`) into a
    readable `sections[2].entries[1]` style path."""
    return json_path.replace("$", "")


def _format_error(err: Any) -> str:
    path = _clean_path(err.json_path)
    msg = err.message
    # jsonschema says things like "in the scope of 'type'"; enrich required
    # property messages with the canonical name when it's an obvious typo.
    if "required property" in msg and "'" in msg:
        for alias, canon in _CANONICAL.items():
            if f"'{alias}'" in msg:
                msg = msg.replace(f"'{alias}'", f"'{canon}' (¿quisiste decir {canon}?)")
                break
    if path:
        return f"{path}: {msg}"
    return msg


def validate_data(data: Any) -> list[str]:
    """Return a list of readable validation errors. Empty list = valid."""
    if jsonschema is None:  # pragma: no cover
        raise RuntimeError("jsonschema is required to validate base_cv.yaml")
    schema = load_schema()
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    return [_format_error(e) for e in errors]


def validate_yaml_file(path: Path) -> list[str]:
    """Load `path` as YAML and validate it. Returns readable errors."""
    p = Path(path)
    if not p.exists():
        return [f"no existe el archivo {p}"]
    try:
        import yaml
        raw = p.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except Exception as e:
        return [f"no se pudo leer/parsear el YAML de {p}: {e}"]
    if data is None:
        return [f"el archivo {p} está vacío"]
    return validate_data(data)


def require_valid_yaml(path: Path) -> None:
    """Validate `path`; raise `BaseCvValidationError` with all errors joined
    into one actionable message on failure."""
    errors = validate_yaml_file(path)
    if errors:
        raise BaseCvValidationError(
            "input/base_cv.yaml no valida contra schema/base_cv.schema.json:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


__all__ = [
    "SCHEMA_PATH",
    "BaseCvValidationError",
    "load_schema",
    "validate_data",
    "validate_yaml_file",
    "require_valid_yaml",
]
