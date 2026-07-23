"""Local review server (`python run.py review <job_slug>`).

Serves `cv.html` for in-browser editing and accepts a POST /save with the
edited outerHTML. On save, the server overwrites `cv.html` and calls
`pdf_renderer.render()` to regenerate `cv.pdf` on the same file.

Endpoints:
  GET  /        →  serves the rendered cv.html (with contenteditable fields).
  POST /save    →  receives the edited HTML body. Overwrites cv.html, then
                   regenerates cv.pdf via Playwright. Returns 200 on success.

Run with uvicorn: `python -m src.review.server <output_dir>` or via run.py.
"""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from src.config import settings
from src.render import pdf_renderer
from src.utils.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app(out_dir: Path) -> FastAPI:
    """Build a FastAPI app bound to a specific job output directory."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cv_html = out_dir / "cv.html"

    app = FastAPI(title="auto-tailored-cv review")
    app.state.out_dir = str(out_dir)
    app.state.cv_html = str(cv_html)

    @app.get("/", response_class=HTMLResponse)
    async def serve_cv() -> HTMLResponse:
        if not cv_html.exists():
            return HTMLResponse(
                f"<h1>cv.html no encontrado</h1><p>Esperado en: {cv_html}</p>",
                status_code=404,
            )
        return HTMLResponse(cv_html.read_text(encoding="utf-8"))

    @app.post("/save", response_class=PlainTextResponse)
    async def save_cv(request: Request) -> PlainTextResponse:
        raw = await request.body()
        text = raw.decode("utf-8", errors="replace")
        # Strip the synthetic `<!DOCTYPE html>\n` prefix that the client
        # prepends to `document.documentElement.outerHTML`. The renderer
        # already adds a doctype; either way the browser tolerates a single
        # one, but we normalize for tidiness.
        text = text.lstrip()
        if text.lower().startswith("<!doctype"):
            nl = text.find(">")
            if nl != -1:
                text = text[nl + 1 :]
        text = "<!DOCTYPE html>\n" + text.lstrip()
        cv_html.write_text(text, encoding="utf-8")
        # Regenerate the PDF on a background thread so the user doesn't have
        # to wait for the HTTP response.
        t = threading.Thread(target=_regenerate_pdf, args=(cv_html,), daemon=True)
        t.start()
        return PlainTextResponse("ok", status_code=200)

    return app


def _regenerate_pdf(cv_html: Path) -> None:
    """Run pdf_renderer on the updated cv.html; best-effort (logs on failure)."""
    try:
        result = pdf_renderer.render(cv_html)
        if not result.success:
            log.error("PDF regeneration failed: %s", result.error)
    except Exception as e:  # pragma: no cover
        log.error("PDF regeneration raised: %s", e)


def run_server(
    job_slug: str,
    *,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
) -> None:
    """Resolve `output/<job_slug>/`, start uvicorn, optionally open the browser."""
    import uvicorn  # local import keeps the module importable in CI without uvicorn

    out_dir = settings.output_dir / job_slug
    if not out_dir.exists():
        raise FileNotFoundError(
            f"output folder not found for job '{job_slug}': {out_dir}. "
            f"Run `python run.py tailor --job <url>` or check the slug."
        )
    cv_html = out_dir / "cv.html"
    if not cv_html.exists():
        raise FileNotFoundError(
            f"cv.html not found in {out_dir}. Tailor the job first with "
            "`python run.py tailor`."
        )

    host = host or settings.review_host
    port = port or settings.review_port
    app = create_app(out_dir)
    url = f"http://{host}:{port}/"

    if open_browser:
        # Small delay so uvicorn has time to start accepting.
        threading.Thread(
            target=lambda: (time.sleep(1.0), webbrowser.open(url)),
            daemon=True,
        ).start()

    log.info("review server on %s — serving %s", url, cv_html)
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main(argv: list | None = None) -> int:
    """Standalone entry: `python -m src.review.server <job_slug>`."""
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(prog="review")
    parser.add_argument("job_slug", help="Job slug (folder name under output/)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_server(
            args.job_slug,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except FileNotFoundError as e:
        log.error(str(e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["create_app", "run_server", "main"]