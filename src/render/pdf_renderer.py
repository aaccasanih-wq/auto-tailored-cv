"""Render a `cv.html` into `cv.pdf` using Playwright (Chromium headless).

The pipeline produces `output/<job_slug>/cv.html` first (via html_renderer), and
this module generates `cv.pdf` next to it using:
    page.pdf(format='Letter', print_background=True)
which respects the `@page { size: Letter; margin: ... }` rule in cv_style.css.

If Chromium isn't installed on this machine, the call fails with a clear error
pointing to `playwright install chromium`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class PdfResult:
    pdf_path: Path | None
    success: bool
    elapsed_seconds: float = 0.0
    error: str = ""


def render(
    html_path: Path,
    pdf_path: Path | None = None,
    *,
    timeout_ms: int = 60_000,
) -> PdfResult:
    """Render the HTML at `html_path` into a PDF. Returns the PDF path."""
    import time

    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    if not html_path.exists():
        return PdfResult(
            pdf_path=None, success=False, elapsed_seconds=0.0,
            error=f"cv.html not found at {html_path}",
        )
    out_path = Path(pdf_path or html_path.with_suffix(".pdf"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    file_url = f"file://{html_path}"
    start = time.time()
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PWError as e:
                if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
                    raise RuntimeError(
                        "Playwright Chromium no está instalado. Corre: "
                        "`python3 -m playwright install chromium`."
                    ) from e
                raise
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(file_url, wait_until="load", timeout=timeout_ms)
                # Make sure the @font-face / CSS finishes loading before the
                # PDF snapshot. `networkidle` is conservative but fine for a
                # static page.
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except PWError:
                    pass
                page.pdf(
                    path=str(out_path),
                    format="Letter",
                    print_background=True,
                )
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except RuntimeError as e:
        elapsed = time.time() - start
        log.error("pdf rendering failed: %s", e)
        return PdfResult(
            pdf_path=None, success=False, elapsed_seconds=elapsed, error=str(e)
        )
    except Exception as e:  # pragma: no cover - environment-specific error
        elapsed = time.time() - start
        log.error("pdf rendering failed: %s", e)
        return PdfResult(
            pdf_path=None, success=False, elapsed_seconds=elapsed, error=str(e)
        )

    elapsed = time.time() - start
    if not out_path.exists() or out_path.stat().st_size == 0:
        return PdfResult(
            pdf_path=None, success=False, elapsed_seconds=elapsed,
            error="Playwright reported success but the PDF is empty or missing.",
        )
    log.info("pdf written: %s in %.2fs", out_path, elapsed)
    return PdfResult(pdf_path=out_path, success=True, elapsed_seconds=elapsed)


def render_pair(
    html_path: Path,
    pdf_path: Path | None = None,
) -> PdfResult:
    """Alias of render(); kept for naming symmetry with html_renderer.render."""
    return render(html_path, pdf_path)


__all__ = ["PdfResult", "render", "render_pair"]