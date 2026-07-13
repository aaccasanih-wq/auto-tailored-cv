"""Configuration loader for auto-tailored-cv.

Reads settings from environment variables (loaded from `.env` via python-dotenv).
The `.env` file is gitignored and never committed. See `.env.example` for the
template.

Usage:
    from src.config import settings
    print(settings.opencode_api_key)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv optional at runtime if env is set externally
    pass


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str, default: str) -> Path:
    raw = _env(name, default)
    if raw is None:
        raw = default
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


@dataclass(frozen=True)
class Settings:
    # --- LLM (OpenCode Go) ---
    opencode_api_key: str
    opencode_base_url: str
    opencode_model_tailor: str
    opencode_model_evaluator: str
    opencode_request_timeout: int

    # --- LinkedIn / Browser MCP ---
    linkedin_saved_jobs_url: str
    browser_timeout_ms: int

    # --- Filesystem ---
    base_cv_path: Path
    jobs_dir: Path
    output_dir: Path

    # --- PDF conversion ---
    soffice_path: str

    # --- Logging ---
    log_level: str

    @property
    def is_configured(self) -> bool:
        return bool(self.opencode_api_key) and "your-opencode-api-key-here" not in self.opencode_api_key


def _load() -> Settings:
    return Settings(
        opencode_api_key=_env("OPENCODE_API_KEY", "") or "",
        opencode_base_url=_env("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1") or "",
        opencode_model_tailor=_env("OPENCODE_MODEL_TAILOR", "glm-5.2") or "glm-5.2",
        opencode_model_evaluator=_env("OPENCODE_MODEL_EVALUATOR", "glm-5.2") or "glm-5.2",
        opencode_request_timeout=_env_int("OPENCODE_REQUEST_TIMEOUT", 120),
        linkedin_saved_jobs_url=_env(
            "LINKEDIN_SAVED_JOBS_URL", "https://www.linkedin.com/my-items/saved-jobs/"
        ) or "",
        browser_timeout_ms=_env_int("BROWSER_TIMEOUT_MS", 15000),
        base_cv_path=_env_path("BASE_CV_PATH", "input/base_cv.docx"),
        jobs_dir=_env_path("JOBS_DIR", "jobs"),
        output_dir=_env_path("OUTPUT_DIR", "output"),
        soffice_path=_env("SOFFICE_PATH", "soffice") or "soffice",
        log_level=_env("LOG_LEVEL", "INFO") or "INFO",
    )


settings = _load()


def ensure_dirs() -> None:
    """Create runtime directories if they don't exist."""
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)


__all__ = ["Settings", "settings", "ensure_dirs", "PROJECT_ROOT"]