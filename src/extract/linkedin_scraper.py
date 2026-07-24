"""Scrape LinkedIn saved jobs via Playwright MCP (default) or Browser MCP (legacy).

Playwright MCP (`@playwright/mcp`) is preferred over Browser MCP because:
  - it has native auto-wait (`browser_wait_for`) resolving the historical
    problem of LinkedIn pages that don't finish loading before the snapshot
    is captured (the root cause of empty / degenerate descriptions on slow
    networks);
  - it supports `--user-data-dir` so the LinkedIn login persists across
    runs (no manual reconnect each session);
  - it's the same Chromium build Playwright uses for `pdf_renderer.py`, so
    there's only one browser to install.

We keep Browser MCP as an optional fallback (`--scraper browsermcp`) during
the transition. The transport layer (`src.extract.mcp_stdio`) is unchanged —
only the launched server and the tool-call sequence differ.

NOTE on LinkedIn TOS: scraping LinkedIn violates their User Agreement. Use this
for personal purposes at your own risk; see README.md for the disclaimer.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.config import settings
from src.extract.mcp_stdio import (
    McpError,
    StdioMcpClient,
    StdioMcpConfig,
    extract_text_content,
)
from src.utils.logging import get_logger

log = get_logger(__name__)


# Regex for LinkedIn job URLs. Matches various formats:
#   https://www.linkedin.com/jobs/view/...
#   https://www.linkedin.com/jobs/view/?currentJobId=1234567890
#   https://www.linkedin.com/jobs/c/rewards/... (saved-jobs sidebar links)
JOB_URL_RE = re.compile(
    r"https?://(?:\w+\.)?linkedin\.com/jobs/"
    r"(?:view/(?:[a-z0-9_-]+-)?(\d+)"
    r"|view/?\?[^\s\"'<>]*?currentJobId=(\d+))",
    re.IGNORECASE,
)

# Matches `currentJobId=NNNN` in ANY LinkedIn jobs URL, including
# `/jobs/search-results/?currentJobId=...` and `/jobs/c/rewards/...?currentJobId=...`.
# Used by `_normalize_job_url` to extract the job ID and produce a canonical
# `/jobs/view/<id>/` URL for the scraper.
_CURRENT_JOB_ID_RE = re.compile(
    r"currentJobId=(\d+)",
    re.IGNORECASE,
)


def _normalize_job_url(url: str) -> str:
    """Normalize any LinkedIn jobs URL containing ``currentJobId=NNNN`` into
    the canonical ``https://www.linkedin.com/jobs/view/<id>/`` form.

    LinkedIn emits several URL patterns that all point to the same job:
      - ``/jobs/view/<title-slug>-<id>/``         (canonical, already OK)
      - ``/jobs/view/?currentJobId=<id>&...``      (canonical, already OK)
      - ``/jobs/search-results/?currentJobId=<id>&...``  (search page — NO)
      - ``/jobs/c/rewards/?currentJobId=<id>&...``       (saved-jobs sidebar — NO)

    The last two are NOT matched by ``JOB_URL_RE`` and would cause the scraper
    to treat the URL as a saved-jobs *listing* (taking a giant snapshot of the
    search results page, which crashes Playwright MCP with a "chunk too long"
    error). This function extracts the job ID from any of them and returns the
    canonical single-job URL so the scraper enters single-job mode.

    If the URL already matches the canonical form or does not contain a
    ``currentJobId`` parameter, it is returned unchanged.
    """
    if not url:
        return url
    if JOB_URL_RE.search(url):
        return url  # Already canonical — /view/<id>/ or /view/?currentJobId=<id>
    m = _CURRENT_JOB_ID_RE.search(url)
    if m:
        job_id = m.group(1)
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    return url


@dataclass
class SavedJob:
    """Public interface for a scraped LinkedIn saved job."""
    title: str
    url: str
    company: str = ""
    location: str = ""
    saved_at_iso: str = ""
    description: str = ""
    # The LinkedIn numeric job id, useful as a stable cache key.
    job_id: str = ""
    # Internal: any parse-time warnings (eg. couldn't find description).
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Snapshot parsing                                                            #
# --------------------------------------------------------------------------- #


def _extract_job_urls(snapshot_text: str) -> list[str]:
    """Return the ordered, de-duplicated list of job URLs found in a snapshot."""
    seen: set = set()
    urls: list[str] = []
    for m in JOB_URL_RE.finditer(snapshot_text):
        job_id = m.group(1) or m.group(2) or ""
        # Normalize URL: prefer the canonical /view/<id>/ form when we have an id.
        if job_id:
            url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        else:
            url = m.group(0)
            url = url.rstrip(".,\"'>() ")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


PAGE_TITLE_RE = re.compile(r'Page Title:\s*(.+?)\s*\|\s*LinkedIn', re.IGNORECASE)
H1_RE = re.compile(r'heading\s+"([^"]+)"\s*\[level=1\]', re.IGNORECASE)
H2_RE = re.compile(r'heading\s+"([^"]+)"\s*\[level=2\]', re.IGNORECASE)


def _extract_heading_h1(snapshot_text: str) -> str:
    """Extract the job title.

    BrowserMCP's flat snapshot prepends `Page Title: <title> | LinkedIn`. We
    prefer that when present. Playwright MCP's `browser_snapshot` returns the
    yaml accessibility tree directly, so we fall back to the first level=1
    `heading "..." [level=1]` node, which is the job title in LinkedIn's DOM.
    """
    m = PAGE_TITLE_RE.search(snapshot_text)
    if m:
        title_field = m.group(1).strip()
        if " | " in title_field:
            return title_field.split(" | ")[0].strip()
        return title_field
    m2 = H1_RE.search(snapshot_text)
    if m2:
        return m2.group(1).strip()
    return ""


def _extract_company(snapshot_text: str, title: str) -> str:
    """Try to extract the company name. Prefer Page Title's middle component,
    then fall back to the first `/company/<slug>/` link in the snapshot.
    """
    m = PAGE_TITLE_RE.search(snapshot_text)
    if m:
        title_field = m.group(1).strip()
        parts = title_field.split(" | ")
        if len(parts) >= 2:
            return parts[1].strip()
    cm = re.search(r'linkedin\.com/company/([a-z0-9\-]+)/', snapshot_text, re.IGNORECASE)
    if cm:
        return cm.group(1).replace("-", " ").title()
    return ""


def _extract_location(snapshot_text: str) -> str:
    """Try patterns like 'text: Lima, Peru' or 'text: Remote' — short location lines."""
    for line in snapshot_text.splitlines():
        t = line.strip()
        if t.lower().startswith("text:"):
            text = t[5:].strip(' "\'')
        else:
            m = re.match(r'-\s+text\s+"([^"]+)"', t)
            if not m:
                continue
            text = m.group(1).strip()
        if not text or len(text) > 80:
            continue
        if ("Remoto" in text or "Híbrido" in text or "Presencial" in text
            or ("," in text and len(text.split()) <= 5)
            or (text.startswith("Lima"))):
            return text
    return ""


def _extract_description(snapshot_text: str) -> str:
    """Pull the job description from a yaml-structured accessibility snapshot.

    LinkedIn renders the description under:
        - heading "Acerca del empleo" [level=2] (Spanish) OR
        - heading "About the job" [level=2] (English)
    and the description block continues until the next level=2 heading.

    The block is made of `- paragraph` and `- text:` nodes, with optional
    `- list > listitem` children. We descend through them in document order
    and concatenate, restoring bullets/structure for readability.
    """
    markers = ("Acerca del empleo", "About the job")
    snapshot_lower = snapshot_text.lower()
    marker_idx = -1
    for marker in markers:
        idx = snapshot_lower.find(marker.lower())
        if idx != -1:
            marker_idx = idx
            break
    if marker_idx != -1:
        line_end = snapshot_text.find("\n", marker_idx)
        if line_end == -1:
            line_end = len(snapshot_text)
        body = snapshot_text[line_end + 1:]
        m = H2_RE.search(body)
        if m:
            body = body[: m.start()]
        collected: list[str] = []
        for raw in body.splitlines():
            s = raw
            if not s.strip():
                continue
            stripped = s.lstrip("- ")
            stripped = re.sub(r'\s*\[ref=[^\]]*\]', '', stripped)
            stripped = re.sub(r'\s*\[level=\d+\]', '', stripped)
            m2 = re.match(r'^(?:text|paragraph|listitem|strong)\b\s*:?\s*(.*)$', stripped, re.IGNORECASE)
            content: str | None = None
            if m2:
                c = m2.group(1).strip()
                c = re.sub(r'\s*\[ref=[^\]]*\]\s*$', '', c)
                c = c.strip(' "\'')
                if c:
                    content = c
            if content is None:
                m3 = re.match(r'^(?:text|strong|paragraph|listitem)\s+"([^"]+)"', stripped, re.IGNORECASE)
                if m3:
                    content = m3.group(1).strip()
            if content:
                if re.match(r'^(?:listitem)\b', stripped, re.IGNORECASE):
                    collected.append(f"- {content}")
                else:
                    collected.append(content)
        if collected:
            return "\n".join(collected).strip()

    # ---- Flat snapshot fallback (BrowserMCP) --------------------------------- #
    seen_start = False
    nav_trivia = {
        "Inicio", "Mi red", "Empleos", "Mensajes", "Notificaciones",
        "Sales Nav", "Yo", "Para negocios", "Acerca de", "Ayuda",
        "Cerrar", "Aceptar", "Saltar al contenido", "Pasar al contenido principal",
    }
    stop_phrases = (
        "Ver empleos similares", "Solicitar", "Aplicar ahora", "Aplicar con",
        "See similar jobs", "Apply", "About us", "Acerca de nosotros",
        "Promocionado por", "Promedio de antigüedad", "Ha contratado a",
        "Desactivado",
    )
    description_lines: list[str] = []
    for line in snapshot_text.splitlines():
        t = line.strip()
        if not t:
            continue
        if t.startswith("```"):
            continue
        if t.startswith("Page URL") or t.startswith("Page Title") or t.startswith("Page Snapshot"):
            seen_start = True
            continue
        if not seen_start:
            continue
        if t.startswith("/url:"):
            continue
        if t.lower().startswith("text:"):
            text = t[5:].strip(' "\'')
        else:
            m = re.match(r'-\s+text\s+"([^"]+)"', t)
            if not m:
                continue
            text = m.group(1).strip()
        if not text or text in nav_trivia:
            continue
        if any(stop in text for stop in stop_phrases):
            break
        if len(text) <= 2:
            continue
        description_lines.append(text)
    return "\n".join(description_lines).strip()


def _parse_job_detail(snapshot_text: str, url: str) -> SavedJob:
    title = _extract_heading_h1(snapshot_text)
    company = _extract_company(snapshot_text, title) if title else ""
    location = _extract_location(snapshot_text)
    description = _extract_description(snapshot_text)

    job_id = ""
    m = JOB_URL_RE.search(url) if url else None
    if m:
        job_id = m.group(1) or m.group(2) or ""

    warnings: list[str] = []
    if not title:
        warnings.append("title not found")

    # LinkedIn sometimes marks a job as "Respuestas gestionadas fuera de
    # LinkedIn" / "Apply on company website" — in those cases the description
    # is hosted externally and LinkedIn itself doesn't show it.
    is_external_apply = (
        "Respuestas gestionadas fuera de LinkedIn" in snapshot_text
        or "Apply on company website" in snapshot_text
        or "Solicitar en el sitio web de la empresa" in snapshot_text
    )
    external_url_match = re.search(
        r"linkedin\.com/safety/go/\?url=([^&\s]+)",
        snapshot_text,
    )
    external_url = ""
    if external_url_match:
        from urllib.parse import unquote
        external_url = unquote(external_url_match.group(1))

    if not description:
        if is_external_apply:
            warnings.append("external_apply_no_description")
        else:
            warnings.append("description not found")
    elif len(description) < 100:
        warnings.append(f"short description ({len(description)} chars)")

    if not description and is_external_apply:
        description = (
            "[LinkedIn no aloja la descripción completa. "
            "Esta oferta redirige a un sitio externo para aplicar.]\n"
            f"URL externa: {external_url}" if external_url
            else "[LinkedIn no aloja la descripción completa para esta oferta.]"
        )

    job = SavedJob(
        title=title,
        url=url,
        company=company,
        location=location,
        description=description,
        job_id=job_id,
        warnings=warnings,
    )
    if external_url:
        job.warnings.append(f"external_url:{external_url}")
    return job


# --------------------------------------------------------------------------- #
# MCP config builders                                                         #
# --------------------------------------------------------------------------- #


def _profile_has_cookies(profile_dir: Path) -> bool:
    """Return True when the persisted Chromium user-data-dir already contains
    a non-empty cookies store — i.e. a real logged-in session.

    Used to decide between headed mode (first run — user has to log into
    LinkedIn manually) and headless mode (subsequent runs — session is
    persisted and we don't need a visible browser window).

    Chromium creates `<profile>/Default/Cookies` (SQLite file) the moment a
    context opens, even without any login — so its mere existence is NOT a
    reliable signal. We require the file to be non-trivially sized (> 2 KB),
    which means it actually contains at least one cookie (LinkedIn sets
    several `li_*` cookies on the logged-in pages).
    """
    profile_dir = Path(profile_dir)
    if not profile_dir.exists():
        return False
    cookies_file = profile_dir / "Default" / "Cookies"
    if not cookies_file.exists():
        return False
    try:
        return cookies_file.stat().st_size > 2048
    except OSError:
        return False


def _build_playwright_mcp_config(force_headed: bool = False) -> StdioMcpConfig:
    """Build the stdio config for @playwright/mcp with a persistent profile.

    The `--user-data-dir` flag persists cookies so the LinkedIn login survives
    across runs. On the FIRST run (when the profile directory has no cookies
    yet) we launch the browser HEADED so you can log into LinkedIn manually;
    once the profile has cookies, subsequent runs are HEADLESS automatically.

    Pass `force_headed=True` to always run with a visible window (useful for
    debugging, or when you suspect the session expired and want to re-login).
    """
    import os
    env = dict(os.environ)
    env.setdefault("npm_config_cache", str(Path.home() / ".npm-user-cache"))
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")

    from src.config import PROJECT_ROOT
    user_data_dir = settings.playwright_user_data_dir
    if not Path(user_data_dir).is_absolute():
        user_data_dir = str(PROJECT_ROOT / settings.playwright_user_data_dir)

    profile_path = Path(user_data_dir)
    has_cookies = _profile_has_cookies(profile_path)
    headless = (not force_headed) and has_cookies
    if not headless:
        log.warning(
            "Lanzando Chromium HEADED (visible) para que te loguees en LinkedIn. "
            "Iniciá sesión en la ventana que se abre; cuando LinkedIn muestre "
            "tu páginas de saved jobs (o cualquier página logueada), el scraper "
            "continúa. La sesión se persiste en %s y próximas corridas ya "
            "serán headless.",
            user_data_dir,
        )

    extra_args = ["--user-data-dir", user_data_dir]
    if headless:
        extra_args.append("--headless")

    return StdioMcpConfig(
        command=settings.playwright_mcp_command,
        args=list(settings.playwright_mcp_args) + extra_args,
        env=env,
    )


def _build_browser_mcp_config() -> StdioMcpConfig:
    """Build the stdio config for the legacy Browser MCP server."""
    import os
    env = dict(os.environ)
    env.setdefault("npm_config_cache", str(Path.home() / ".npm-user-cache"))
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return StdioMcpConfig(
        command=settings.browser_mcp_command,
        args=list(settings.browser_mcp_args),
        env=env,
    )


def _build_mcp_config(backend: str = "playwright") -> StdioMcpConfig:
    backend = (backend or settings.scraper_backend).lower()
    if backend == "browsermcp":
        log.info("scraper backend: browsermcp (legacy)")
        return _build_browser_mcp_config()
    log.info("scraper backend: playwright (auto-wait via browser_wait_for)")
    return _build_playwright_mcp_config()


def _is_headless(config: StdioMcpConfig) -> bool:
    """Return True if the MCP process will be launched in headless mode."""
    return "--headless" in config.args


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


# Markers used by `browser_wait_for` to auto-wait for content on each LinkedIn
# page. Playwright MCP resolves `browser_wait_for` natively (it polls until the
# text appears in the DOM), eliminating the historical "page didn't finish
# loading" silent failures.
SAVED_JOBS_MARKER_TEXT = "Empleos guardados"
JOB_DETAIL_MARKERS = ("Acerca del empleo", "About the job")


def _strip_url_for_tool(url: str) -> str:
    """Some MCP variants refuse trailing slashes — keep it simple."""
    return url


SEE_MORE_RE = re.compile(
    r'(?:button|link|generic)\s+"(\.{0,3}\s*(?:más|see more|view more|ver más))"'
    r'\s*\[ref=(e\d+)\]',
    re.IGNORECASE,
)


async def _try_click_see_more(
    client: StdioMcpClient,
    snap: str,
    tool_names: list[str],
) -> bool:
    """Try to click the '...más' / 'See more' button on a LinkedIn job page
    to expand the truncated job description. Returns True if a click was made.

    LinkedIn truncates long job descriptions and shows a '...más' button.
    Without clicking it, the snapshot only contains the first ~400 chars of
    the description. We search the snapshot for the button, extract its ref,
    and call `browser_click` with it.
    """
    if "browser_click" not in tool_names:
        return False
    m = SEE_MORE_RE.search(snap)
    if not m:
        return False
    ref = m.group(2)
    try:
        await client.call_tool(
            "browser_click",
            {"element": m.group(1), "ref": ref},
            timeout_s=15,
        )
        await asyncio.sleep(1.5)
        return True
    except McpError as e:
        log.debug("click see-more returned: %s", str(e)[:120])
        return False
    except Exception:
        return False


async def _navigate_and_wait(
    client: StdioMcpClient,
    url: str,
    wait_marker: str | None,
    nav_delay_s: int,
    tool_names: list[str],
    timeout_wait: int = 30,
    timeout_nav: int = 60,
    try_see_more: bool = False,
) -> str:
    """Navigate, then wait for a DOM marker if the tool is available, otherwise
    fall back to a fixed sleep. Returns the snapshot text.

    If `try_see_more` is True, after the initial snapshot we look for a
    '...más' / 'See more' button and click it to expand the full job
    description, then re-snapshot.
    """
    try:
        await client.call_tool(
            "browser_navigate", {"url": _strip_url_for_tool(url)}, timeout_s=timeout_nav
        )
    except McpError as e:
        msg = str(e)
        if "No connection" in msg or "tab is not" in msg:
            log.warning("browser_navigate failed (%s) — try direct snapshot", msg[:120])
        else:
            raise
    # Auto-wait when possible.
    waited = False
    if wait_marker and "browser_wait_for" in tool_names:
        try:
            await client.call_tool(
                "browser_wait_for",
                {"text": wait_marker, "time": 1},
                timeout_s=timeout_wait,
            )
            waited = True
        except McpError as e:
            log.warning("browser_wait_for('%s') returned: %s — proceeding", wait_marker, str(e)[:120])
    if not waited:
        await asyncio.sleep(max(nav_delay_s, 6))
    snap = extract_text_content(await client.call_tool("browser_snapshot", {}, timeout_s=60))
    if try_see_more:
        clicked = await _try_click_see_more(client, snap, tool_names)
        if clicked:
            log.debug("expanded job description via 'see more' — re-snapshotting")
            if wait_marker and "browser_wait_for" in tool_names:
                try:
                    await client.call_tool(
                        "browser_wait_for",
                        {"text": wait_marker, "time": 1},
                        timeout_s=15,
                    )
                except McpError:
                    pass
            snap = extract_text_content(
                await client.call_tool("browser_snapshot", {}, timeout_s=60)
            )
    return snap


async def _scrape_single_job(
    job_url: str,
    backend: str = "playwright",
    nav_delay_s: int | None = None,
) -> list[SavedJob]:
    """Scrape a single LinkedIn job posting directly (no saved-jobs listing).

    Used when `--job <url>` points to a job posting. Navigates ONCE to the
    job URL, clicks 'see more' to expand the description, retries on empty
    snapshots, and returns a 1-element list. Works for any LinkedIn job URL
    (saved or not) as long as the persistent session can view it.
    """
    nav_delay = nav_delay_s if nav_delay_s is not None else settings.browser_nav_delay_s
    config = _build_mcp_config(backend)
    headless_mode = _is_headless(config)
    wait_timeout_sec = 30 if headless_mode else 300
    client = StdioMcpClient(config)
    await client.start()
    try:
        await client.initialize()
        tools = await client.list_tools()
        tool_names = [t.get("name", "") for t in tools]
        if "browser_navigate" not in tool_names or "browser_snapshot" not in tool_names:
            raise RuntimeError(
                f"MCP server didn't expose browser_navigate/browser_snapshot — "
                f"available tools: {tool_names}."
            )
        log.info("navigating to job %s", job_url)
        snap = await _navigate_and_wait(
            client,
            job_url,
            wait_marker=JOB_DETAIL_MARKERS[0] if "browser_wait_for" in tool_names else None,
            nav_delay_s=nav_delay,
            tool_names=tool_names,
            timeout_wait=wait_timeout_sec,
            try_see_more=True,
        )
        # Login-wall detection (same logic as the listing flow).
        snapshot_lower = snap.lower()
        if ("sign in" in snapshot_lower or "iniciar sesión" in snapshot_lower
                or "/login" in snap.lower() or "/checkpoint" in snap.lower()):
            if headless_mode:
                log.error(
                    "LinkedIn shows the login page — no session in %s. Run "
                    "`python run.py login` first.",
                    settings.playwright_user_data_dir,
                )
                return []
            log.warning("Login required — log in in the open window.")
            for attempt in range(30):
                await asyncio.sleep(20)
                try:
                    snap = extract_text_content(
                        await client.call_tool("browser_snapshot", {}, timeout_s=60)
                    )
                except Exception:
                    continue
                low = snap.lower()
                if ("sign in" not in low and "iniciar sesión" not in low
                        and "/login" not in snap.lower()
                        and "/checkpoint" not in snap.lower()):
                    break
            else:
                log.error("No login detected after 10 min.")
                return []

        job = _parse_job_detail(snap, job_url)
        # Retry up to 2 times if description is missing (same as the listing flow).
        retries = 0
        while ("description not found" in job.warnings
               or ("short description" in " ".join(job.warnings) and not job.description)) \
                and retries < 2:
            retries += 1
            alt_marker = JOB_DETAIL_MARKERS[retries % len(JOB_DETAIL_MARKERS)]
            log.info("retry %d for %s (waiting for '%s')", retries, job_url, alt_marker)
            if "browser_wait_for" in tool_names:
                try:
                    await client.call_tool(
                        "browser_wait_for", {"text": alt_marker, "time": 2},
                        timeout_s=30,
                    )
                except McpError as e:
                    log.warning("wait returned: %s; falling back to sleep", str(e)[:120])
                    await asyncio.sleep(6)
            else:
                await asyncio.sleep(6)
            snap = extract_text_content(
                await client.call_tool("browser_snapshot", {}, timeout_s=60)
            )
            await _try_click_see_more(client, snap, tool_names)
            snap = extract_text_content(
                await client.call_tool("browser_snapshot", {}, timeout_s=60)
            )
            job = _parse_job_detail(snap, job_url)
        if job.warnings:
            log.warning("parse warnings for %s: %s", job_url, job.warnings)
        return [job]
    finally:
        try:
            await client.call_tool("browser_close", {}, timeout_s=10)
        except Exception:
            pass
        await client.close()


async def extract_saved_jobs(
    saved_jobs_url: str = "",
    nav_delay_s: int | None = None,
    on_progress: Any | None = None,
    backend: str = "playwright",
) -> list[SavedJob]:
    """Connect to the configured MCP server and scrape every saved job posting.

    Parameters
    ----------
    saved_jobs_url
        Defaults to settings.linkedin_saved_jobs_url.
    nav_delay_s
        Override of the inter-page wait for slow networks. With Playwright
        MCP the auto-wait (`browser_wait_for`) makes this largely redundant.
    on_progress
        Optional callback `on_progress(done, total, job)` invoked after each job.
    backend
        "playwright" (default; uses `@playwright/mcp` + user-data-dir) or
        "browsermcp" (legacy; uses `@browsermcp/mcp` + Chrome extension).
    """
    saved_jobs_url = saved_jobs_url or settings.linkedin_saved_jobs_url
    nav_delay = nav_delay_s if nav_delay_s is not None else settings.browser_nav_delay_s

    # Normalize search-results / rewards URLs that carry a currentJobId param
    # into the canonical /jobs/view/<id>/ form so the scraper enters
    # single-job mode instead of trying to snapshot a giant search page.
    original_url = saved_jobs_url
    saved_jobs_url = _normalize_job_url(saved_jobs_url)
    if saved_jobs_url != original_url:
        log.info("normalized job URL: %s → %s", original_url, saved_jobs_url)

    # Single-job mode: when the target URL itself is a LinkedIn job posting
    # (e.g. `--job https://www.linkedin.com/jobs/view/4431977634/`), skip the
    # saved-jobs listing entirely and scrape just that one job. This works
    # for ANY LinkedIn job URL (saved or not) as long as the logged-in
    # session can view it.
    if JOB_URL_RE.search(saved_jobs_url or ""):
        log.info("single-job mode — scraping %s directly", saved_jobs_url)
        return await _scrape_single_job(
            saved_jobs_url, backend=backend, nav_delay_s=nav_delay,
        )

    config = _build_mcp_config(backend)
    headless_mode = _is_headless(config)
    # In headed mode (first run / no cookies yet), allow up to 5 minutes for the
    # user to log into LinkedIn manually before the auto-wait times out.
    wait_timeout_sec = 30 if headless_mode else 300
    client = StdioMcpClient(config)
    await client.start()
    try:
        await client.initialize()
        tools = await client.list_tools()
        tool_names = [t.get("name", "") for t in tools]
        log.info("MCP exposes %d tools", len(tools))
        log.debug("tool names: %s", tool_names)
        if "browser_navigate" not in tool_names or "browser_snapshot" not in tool_names:
            raise RuntimeError(
                f"MCP server didn't expose browser_navigate/browser_snapshot — "
                f"available tools: {tool_names}. Make sure the right MCP server is "
                f"running (backend={backend})."
            )
        # 1. List saved jobs on the saved-jobs page.
        log.info("navigating to %s", saved_jobs_url)
        list_snapshot = await _navigate_and_wait(
            client,
            saved_jobs_url,
            wait_marker=SAVED_JOBS_MARKER_TEXT if "browser_wait_for" in tool_names else None,
            nav_delay_s=nav_delay,
            tool_names=tool_names,
            timeout_wait=wait_timeout_sec,
        )
        if not list_snapshot.strip():
            log.error("empty snapshot — tab not on a valid page?")
            return []
        # Detect login-wall: LinkedIn redirected us to /login or /checkpoint.
        snapshot_lower = list_snapshot.lower()
        is_login_wall = (
            "sign in" in snapshot_lower or "iniciar sesión" in snapshot_lower
            or "/login" in list_snapshot.lower() or "/checkpoint" in list_snapshot.lower()
        )
        if is_login_wall:
            if headless_mode:
                log.error(
                    "LinkedIn está mostrando la página de login — no hay sesión "
                    "iniciada en el perfil persistente (%s). Ejecutá primero "
                    "`python run.py login` para abrir Chromium headed, iniciá "
                    "sesión manualmente, cerrá el navegador, y volvé a correr "
                    "`python run.py all` (las próximas corridas ya recordarán "
                    "la sesión).",
                    settings.playwright_user_data_dir,
                )
                return []
            # Headed mode: keep the browser open and wait for the user to log
            # in. Poll the page every ~20 s, up to 10 minutes total. Once the
            # saved-jobs page comes up (no /login, no /checkpoint in URL, and
            # "Empleos guardados" or "Saved jobs" visible), proceed.
            log.warning(
                "La página de saved jobs requiere login. Tenés hasta 10 min "
                "para iniciar sesión en la ventana del navegador que abrió. "
                "Una vez que LinkedIn muestre tu página de saved-jobs, el "
                "scraper continúa solo."
            )
            logged_in = False
            for attempt in range(30):  # ~30 * 20s = 10 min
                await asyncio.sleep(20)
                try:
                    list_snapshot = extract_text_content(
                        await client.call_tool("browser_snapshot", {}, timeout_s=60)
                    )
                except Exception:
                    continue
                snapshot_lower = list_snapshot.lower()
                if ("sign in" not in snapshot_lower
                    and "iniciar sesión" not in snapshot_lower
                    and "/login" not in list_snapshot.lower()
                    and "/checkpoint" not in list_snapshot.lower()
                    and ("empleos guardados" in snapshot_lower
                         or "saved jobs" in snapshot_lower
                         or JOB_URL_RE.search(list_snapshot))):
                    logged_in = True
                    break
                if attempt % 3 == 2:
                    log.info("aguardando login... (%d s transcurridos)", (attempt + 1) * 20)
            if not logged_in:
                log.error(
                    "No se detectó login después de 10 min. Cerrá el navegador "
                    "vos también y volvé a ejecutar `python run.py login` para "
                    "loguearte con calma."
                )
                return []
            log.info("login detectado — continuando con el scraping...")
        job_urls = _extract_job_urls(list_snapshot)
        log.info("found %d saved-job URLs in snapshot", len(job_urls))

        # 2. Visit each job page and parse.
        jobs: list[SavedJob] = []
        for idx, url in enumerate(job_urls, start=1):
            log.info("[%d/%d] navigating to %s", idx, len(job_urls), url)
            try:
                wait_marker = JOB_DETAIL_MARKERS[0] if "browser_wait_for" in tool_names else None
                snap = await _navigate_and_wait(
                    client,
                    url,
                    wait_marker=wait_marker,
                    nav_delay_s=nav_delay,
                    tool_names=tool_names,
                    try_see_more=True,
                )
                job = _parse_job_detail(snap, url)
                retries = 0
                while ("description not found" in job.warnings
                       or ("short description" in " ".join(job.warnings) and not job.description)) \
                        and retries < 2:
                    retries += 1
                    alt_marker = JOB_DETAIL_MARKERS[retries % len(JOB_DETAIL_MARKERS)]
                    log.info(
                        "snapshot retry %d for %s (waiting for '%s')",
                        retries, url, alt_marker,
                    )
                    if "browser_wait_for" in tool_names:
                        try:
                            await client.call_tool(
                                "browser_wait_for",
                                {"text": alt_marker, "time": 2},
                                timeout_s=30,
                            )
                        except McpError as e:
                            log.warning("wait returned: %s; falling back to sleep", str(e)[:120])
                            await asyncio.sleep(6)
                    else:
                        await asyncio.sleep(6)
                    # Also try clicking 'see more' again on retry.
                    snap = extract_text_content(
                        await client.call_tool("browser_snapshot", {}, timeout_s=60)
                    )
                    await _try_click_see_more(client, snap, tool_names)
                    snap = extract_text_content(
                        await client.call_tool("browser_snapshot", {}, timeout_s=60)
                    )
                    job = _parse_job_detail(snap, url)
                if job.warnings:
                    log.warning("parse warnings for %s: %s", url, job.warnings)
                jobs.append(job)
                if on_progress:
                    on_progress(idx, len(job_urls), job)
            except McpError as e:
                log.warning("MCP error on %s: %s", url, e)
                jobs.append(SavedJob(title="", url=url, warnings=[f"mcp_error: {e}"]))
            except Exception as e:
                log.warning("scrape failed for %s: %s", url, e)
                jobs.append(SavedJob(title="", url=url, warnings=[f"scrape_error: {e}"]))
        return jobs
    finally:
        try:
            await client.call_tool("browser_close", {}, timeout_s=10)
        except Exception:
            pass
        await client.close()


def scrape_saved_jobs() -> list[SavedJob]:
    """Sync wrapper. Returns all saved jobs from LinkedIn via the configured MCP."""
    return asyncio.run(extract_saved_jobs())


def save_jobs_json(jobs: list[SavedJob], path: Path) -> Path:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([j.to_dict() for j in jobs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


__all__ = [
    "SavedJob",
    "extract_saved_jobs",
    "scrape_saved_jobs",
    "save_jobs_json",
    "_extract_job_urls",
    "_parse_job_detail",
    "_scrape_single_job",
]