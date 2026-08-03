"""Configuration loader for auto-tailored-cv.

Reads settings from environment variables (loaded from `.env` via python-dotenv).
The `.env` file is gitignored and never committed. See `.env.example` for the
template.

The LLM client uses the official `openai` SDK pointed at a configurable
OpenAI-compatible endpoint (LLM_BASE_URL), so any provider that speaks the
OpenAI API works: OpenCode Go, DeepSeek, OpenRouter, etc. The defaults keep
pointing at OpenCode Go (https://opencode.ai/zen/go/v1) with DeepSeek V4 Flash.

Usage:
    from src.config import settings
    print(settings.llm_api_key)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str | None:
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    # --- LLM (provider-agnostic OpenAI-compatible endpoint) ---
    # Defaults: OpenCode Go (https://opencode.ai/zen/go/v1) + DeepSeek V4 Flash.
    # Any OpenAI-compatible LLM_BASE_URL will work (DeepSeek, OpenRouter, ...).
    llm_api_key: str
    llm_base_url: str
    llm_model_tailor: str
    llm_model_evaluator: str
    llm_request_timeout: int

    # --- LinkedIn / Browser automation (Playwright MCP default) ---
    linkedin_saved_jobs_url: str
    browser_timeout_ms: int
    # Default scraper backend: "playwright" (new) or "browsermcp" (legacy fallback).
    scraper_backend: str
    # Browser MCP (legacy): NPM command that launches its MCP server.
    browser_mcp_command: str
    browser_mcp_args: list[str]
    # Playwright MCP: NPM command that launches @playwright/mcp.
    playwright_mcp_command: str
    playwright_mcp_args: list[str]
    # Persistent user-data-dir so the LinkedIn login survives across runs.
    playwright_user_data_dir: str
    browser_nav_delay_s: int

    # --- Filesystem ---
    base_cv_path: Path
    jobs_dir: Path
    output_dir: Path
    templates_dir: Path
    # Optional plain-text file with the user's personal LLM instructions
    # (comments/blank lines ignored). See src/profile/preferences.py.
    preferences_path: Path

    # --- Pipeline behavior ---
    # When False, skip the evaluate + repair LLM passes entirely (tailor →
    # render). Default True: catches hallucinations / verbatim copying, which
    # matters when the CV goes to a real employer.
    enable_evaluation: bool

    # --- PDF conversion ---
    # Path to the LibreOffice `soffice` binary. Only used by the legacy
    # `--legacy-docx` flag; the new HTML pipeline renders PDFs via Playwright.
    soffice_path: str

    # --- Review server ---
    review_host: str
    review_port: int

    # --- Logging ---
    log_level: str

    @property
    def is_configured(self) -> bool:
        placeholder = "your-llm-api-key-here"
        return bool(self.llm_api_key) and placeholder not in self.llm_api_key


def _load() -> Settings:
    return Settings(
        llm_api_key=_env("LLM_API_KEY", "") or "",
        llm_base_url=_env("LLM_BASE_URL", "https://opencode.ai/zen/go/v1") or "",
        llm_model_tailor=_env("LLM_MODEL_TAILOR", "deepseek-v4-flash") or "deepseek-v4-flash",
        llm_model_evaluator=_env("LLM_MODEL_EVALUATOR", "deepseek-v4-flash") or "deepseek-v4-flash",
        llm_request_timeout=_env_int("LLM_REQUEST_TIMEOUT", 120),
        linkedin_saved_jobs_url=_env(
            "LINKEDIN_SAVED_JOBS_URL", "https://www.linkedin.com/my-items/saved-jobs/"
        ) or "",
        browser_timeout_ms=_env_int("BROWSER_TIMEOUT_MS", 15000),
        scraper_backend=_env("SCRAPER_BACKEND", "playwright") or "playwright",
        browser_mcp_command=_env("BROWSER_MCP_COMMAND", "npx") or "npx",
        browser_mcp_args=(
            _env("BROWSER_MCP_ARGS", "-y @browsermcp/mcp@latest") or "-y @browsermcp/mcp@latest"
        ).split(),
        playwright_mcp_command=_env("PLAYWRIGHT_MCP_COMMAND", "npx") or "npx",
        playwright_mcp_args=(
            _env("PLAYWRIGHT_MCP_ARGS", "-y @playwright/mcp@latest") or "-y @playwright/mcp@latest"
        ).split(),
        playwright_user_data_dir=_env(
            "PLAYWRIGHT_USER_DATA_DIR", ".playwright-profile"
        ) or ".playwright-profile",
        browser_nav_delay_s=_env_int("BROWSER_NAV_DELAY_S", 3),
        base_cv_path=_env_path("BASE_CV_PATH", "input/base_cv.yaml"),
        jobs_dir=_env_path("JOBS_DIR", "jobs"),
        output_dir=_env_path("OUTPUT_DIR", "output"),
        templates_dir=_env_path("TEMPLATES_DIR", "templates"),
        preferences_path=_env_path("PREFERENCES_PATH", "input/preferences.txt"),
        enable_evaluation=_env_bool("ENABLE_EVALUATION", True),
        soffice_path=_env("SOFFICE_PATH", "soffice") or "soffice",
        review_host=_env("REVIEW_HOST", "localhost") or "localhost",
        review_port=_env_int("REVIEW_PORT", 8420),
        log_level=_env("LOG_LEVEL", "INFO") or "INFO",
    )


settings = _load()


def ensure_dirs() -> None:
    """Create runtime directories if they don't exist."""
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    if settings.scraper_backend == "playwright":
        try:
            (PROJECT_ROOT / settings.playwright_user_data_dir).mkdir(
                parents=True, exist_ok=True
            )
        except Exception:
            # Best-effort; mkdir failures (e.g. relative path on different cwd)
            # are surfaced later when the MCP server actually starts.
            pass


__all__ = ["Settings", "settings", "ensure_dirs", "PROJECT_ROOT"]