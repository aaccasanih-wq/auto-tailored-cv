"""Scrape LinkedIn saved jobs using Browser MCP.

Browser MCP is launched as a local MCP server (NodeJS, via `npx @browsermcp/mcp@latest`)
plus a Chrome extension that connects your existing logged-in Chrome profile. Our
code talks to the MCP server over stdio using the minimal JSON-RPC client in
`src.extract.mcp_stdio`.

This scraper:
  1. Launches the MCP subprocess.
  2. Sends `initialize` + `notifications/initialized`.
  3. Calls `tools/list` to enumerate available tools.
  4. Calls `browser_navigate` with `https://www.linkedin.com/my-items/saved-jobs/`.
  5. Calls `browser_snapshot` and regex-parses the accessibility tree for job URLs.
  6. For each job URL, navigates and snapshots again, parsing title/company/
     location/description.

NOTE on LinkedIn TOS: scraping LinkedIn violates their User Agreement. Use this
for personal purposes at your own risk; see README.md for the disclaimer.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- #
# Snapshot parsing                                                            #
# --------------------------------------------------------------------------- #


def _extract_job_urls(snapshot_text: str) -> List[str]:
    """Return the ordered, de-duplicated list of job URLs found in a snapshot."""
    seen: set = set()
    urls: List[str] = []
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


def _strip_yaml_indent(line: str) -> str:
    return line.lstrip(" -").strip()


PAGE_TITLE_RE = re.compile(r'Page Title:\s*(.+?)\s*\|\s*LinkedIn', re.IGNORECASE)


def _extract_heading_h1(snapshot_text: str) -> str:
    """Extract the job title.

    BrowserMCP v0.1.x snapshots are flat text, not the structured yaml tree
    that later versions produce. LinkedIn includes the job title in the page
    <title> tag, rendered by BrowserMCP as the leading line:
        Page Title: <title> | <company?> | LinkedIn
    We prefer that. As a fallback we look for the older yaml-tree 'heading' line.
    """
    m = PAGE_TITLE_RE.search(snapshot_text)
    if m:
        # "Practicante Pro Comercial | apparka" -> take first half
        # Some titles don't have company; some have only title|LinkedIn
        title_field = m.group(1).strip()
        # Split on first ' | ' and take the first chunk as the role
        if " | " in title_field:
            return title_field.split(" | ")[0].strip()
        return title_field
    # Older fallback: yaml tree
    for line in snapshot_text.splitlines():
        if "heading" in line and "level=1" in line:
            m = re.search(r'"([^"]+)"', line)
            if m:
                return m.group(1).strip()
    return ""


def _extract_company(snapshot_text: str, title: str) -> str:
    """Try to extract the company name from the Page Title too — it's the
    middle component of "<title> | <company> | LinkedIn". If that fails, scan
    for a /company/<slug>/ link in the snapshot and use the slug.
    """
    m = PAGE_TITLE_RE.search(snapshot_text)
    if m:
        title_field = m.group(1).strip()
        parts = title_field.split(" | ")
        if len(parts) >= 2:
            return parts[1].strip()
    # Fallback: first /company/<slug>/ URL
    cm = re.search(r'linkedin\.com/company/([a-z0-9\-]+)/', snapshot_text, re.IGNORECASE)
    if cm:
        return cm.group(1).replace("-", " ").title()
    return ""


def _extract_location(snapshot_text: str) -> str:
    """Try patterns like 'text: Lima, Peru' or 'text: Remote' — short location lines."""
    for line in snapshot_text.splitlines():
        t = line.strip()
        # Strip leading 'text:' label
        if t.lower().startswith("text:"):
            text = t[5:].strip(' "\'')
        else:
            # Maybe older tree format
            m = re.match(r'-\s+text\s+"([^"]+)"', t)
            if not m:
                continue
            text = m.group(1).strip()
        if not text or len(text) > 80:
            continue
        # Heuristics for a location: contains a comma + 2-3 words, OR words like
        # 'Remoto', 'Híbrido', 'Presencial', 'Lima', 'Peru', etc.
        if ("Remoto" in text or "Híbrido" in text or "Presencial" in text
            or ("," in text and len(text.split()) <= 5)
            or (text.startswith("Lima"))):
            return text
    return ""


def _extract_description(snapshot_text: str) -> str:
    """Pull the job description from a BrowserMCP v0.1.x yaml-structured
    snapshot.

    LinkedIn renders the description under:
        - heading "Acerca del empleo" [level=2] [ref=...]
    and the description block continues until the next heading of level 2
    (often "Establecer una alerta para empleos similares", "Información
    exclusiva sobre <company>...", etc.).

    The block is made of `- paragraph` and `- text:` nodes, with optional
    `- list > listitem` children. We descend through them in document order
    and concatenate, restoring bullets/structure for readability.

    If the structured marker "Acerca del empleo" / "About the job" isn't
    present, fall back to the earlier flat 'text:' heuristics (still useful
    when BrowserMCP returns a degenerate snapshot).
    """

    # ---- Structured-yaml branch -------------------------------------------- #
    marker_phrase_sp = "Acerca del empleo"
    marker_phrase_en = "About the job"
    markers = (marker_phrase_sp, marker_phrase_en)
    marker_idx = -1
    snapshot_lower = snapshot_text.lower()
    for marker in markers:
        idx = snapshot_lower.find(marker.lower())
        if idx != -1:
            marker_idx = idx
            break
    if marker_idx != -1:
        # Find the end of the marker line.
        line_end = snapshot_text.find("\n", marker_idx)
        if line_end == -1:
            line_end = len(snapshot_text)
        body = snapshot_text[line_end + 1:]
        # Stop at the next level=2 heading if present.
        stop_heading_re = re.compile(r'-\s+heading\s+"([^"]+)"\s*\[level=2\]', re.IGNORECASE)
        m = stop_heading_re.search(body)
        if m:
            body = body[: m.start()]
        # Now collect all the inner text from this slice. We strip yaml
        # tree-dash prefixes and 'ref='/'level='/'textbox' decorations, but
        # preserve the textual payload of paragraph/text/listitem/strong nodes.
        # We collapse runs that share a paragraph (LinkedIn splits bold+regular
        # text into many runs).
        collected: List[str] = []
        for raw in body.splitlines():
            s = raw
            # Skip pure heading/alert nodes
            if not s.strip():
                continue
            # Strip leading dashes and indentation for matching but keep token
            stripped = s.lstrip("- ")
            # Drop '[ref=...]' or '[level=N]' modifiers right after the tag.
            stripped = re.sub(r'\s*\[ref=[^\]]*\]', '', stripped)
            stripped = re.sub(r'\s*\[level=\d+\]', '', stripped)
            # Match `- text: <content>` / `- text "content"` / `text: content`
            m2 = re.match(r'^(?:text|paragraph|listitem|strong)\b\s*:?\s*(.*)$', stripped, re.IGNORECASE)
            content: Optional[str] = None
            if m2:
                c = m2.group(1).strip()
                # Drop trailing [ref=...] modifiers if present
                c = re.sub(r'\s*\[ref=[^\]]*\]\s*$', '', c)
                # Strip wrapping quotes
                c = c.strip(' "\'')
                if c:
                    content = c
            if content is None:
                # Maybe `- strong "..." [ref=...]` form
                m3 = re.match(r'^(?:text|strong|paragraph|listitem)\s+"([^"]+)"', stripped, re.IGNORECASE)
                if m3:
                    content = m3.group(1).strip()
            if content:
                # Bullet-ize list items cleanly
                # Prepend '- ' for listitem rows so the LLM sees bullet structure
                if re.match(r'^(?:listitem)\b', stripped, re.IGNORECASE):
                    collected.append(f"- {content}")
                else:
                    collected.append(content)
        if collected:
            return "\n".join(collected).strip()

    # ---- Flat snapshot fallback -------------------------------------------- #
    seen_start = False
    nav_trivia = {
        "Inicio", "Mi red", "Empleos", "Mensajes", "Notificaciones",
        "Sales Nav", "Yo", "Para negocios", "Acerca de", "Ayuda",
        "Cerrar", "Aceptar", "Saltar al contenido", "Pasar al contenido principal",
    }
    stop_phrases = (
        "Ver empleos similares",
        "Solicitar",
        "Aplicar ahora",
        "Aplicar con",
        "See similar jobs",
        "Apply",
        "About us",
        "Acerca de nosotros",
        "Promocionado por",
        "Promedio de antigüedad",
        "Ha contratado a",
        "Desactivado",
    )
    description_lines: List[str] = []
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

    warnings: List[str] = []
    if not title:
        warnings.append("title not found")

    # LinkedIn sometimes marks a job as "Respuestas gestionadas fuera de
    # LinkedIn" / "Apply on company website" — in those cases the description
    # is hosted externally and LinkedIn itself doesn't show it. We save what
    # we do have (title, company, location) plus the external URL if present,
    # so the user can decide to skip it or visit the URL manually.
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
        # The url parameter is URL-encoded
        from urllib.parse import unquote
        external_url = unquote(external_url_match.group(1))

    if not description:
        if is_external_apply:
            warnings.append("external_apply_no_description")
        else:
            warnings.append("description not found")
    elif len(description) < 100:
        warnings.append(f"short description ({len(description)} chars)")

    # Compose a useful placeholder description when LinkedIn hosts none
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
    # Stash the external apply URL for downstream tools
    if external_url:
        job.warnings.append(f"external_url:{external_url}")
    return job


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


def _build_mcp_config() -> StdioMcpConfig:
    # BrowserMCP runs via `npx -y @browsermcp/mcp@latest`, which downloads
    # the package to npm's cache. On this Mac the default cache (~/.npm)
    # has permission issues; we always redirect to a user-owned cache so
    # `npx` doesn't fail with EACCES/EEXIST mid-scrape.
    import os
    env = dict(os.environ)
    env.setdefault(
        "npm_config_cache", str(Path.home() / ".npm-user-cache")
    )
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return StdioMcpConfig(
        command=settings.browser_mcp_command,
        args=list(settings.browser_mcp_args),
        env=env,
    )


async def extract_saved_jobs(
    saved_jobs_url: str = "",
    nav_delay_s: Optional[int] = None,
    on_progress: Optional[Any] = None,
) -> List[SavedJob]:
    """Connect to Browser MCP and scrape every saved job posting.

    Parameters
    ----------
    saved_jobs_url
        Defaults to settings.linkedin_saved_jobs_url.
    nav_delay_s
        Override of the inter-page wait for slow networks.
    on_progress
        Optional callback `on_progress(done, total, job)` invoked after each job.
    """
    saved_jobs_url = saved_jobs_url or settings.linkedin_saved_jobs_url
    nav_delay = nav_delay_s if nav_delay_s is not None else settings.browser_nav_delay_s

    config = _build_mcp_config()
    client = StdioMcpClient(config)
    await client.start()
    try:
        await client.initialize()
        tools = await client.list_tools()
        tool_names = [t.get("name", "") for t in tools]
        log.info("BrowserMCP exposes %d tools", len(tools))
        log.debug("tool names: %s", tool_names)
        if "browser_navigate" not in tool_names or "browser_snapshot" not in tool_names:
            raise RuntimeError(
                "BrowserMCP didn't expose browser_navigate/browser_snapshot — "
                f"available tools: {tool_names}. Make sure the Chrome extension "
                "is installed and a tab is connected. See README.md."
            )
        # 1. List saved jobs.
        log.info("navigating to %s", saved_jobs_url)
        try:
            await client.call_tool("browser_navigate", {"url": saved_jobs_url}, timeout_s=60)
            await asyncio.sleep(max(nav_delay, 5))
        except McpError as e:
            # Common case on the very first run: the BrowserMCP extension
            # reports "No connection to browser extension" because Chrome
            # hasn't fully connected yet. Fall back to a snapshot of the
            # currently-focused tab, which is often already on the right
            # page (because the user pinned and navigated there manually).
            msg = str(e)
            log.warning("browser_navigate failed: %s — attempting snapshot of current tab", msg[:120])
        try:
            list_snapshot = extract_text_content(
                await client.call_tool("browser_snapshot", {}, timeout_s=60)
            )
        except McpError as e:
            log.error("browser_snapshot also failed: %s", e)
            return []
        if not list_snapshot.strip():
            log.error("empty snapshot — tab not on a valid page?")
            return []
        job_urls = _extract_job_urls(list_snapshot)
        log.info("found %d saved-job URLs in snapshot", len(job_urls))

        # 2. Visit each job page and parse.
        jobs: List[SavedJob] = []
        for idx, url in enumerate(job_urls, start=1):
            log.info("[%d/%d] navigating to %s", idx, len(job_urls), url)
            try:
                await client.call_tool("browser_navigate", {"url": url}, timeout_s=60)
                # LinkedIn renders the description block lazily (~3-10s after
                # the DOM is interactive). We wait nav_delay, snapshot, and if
                # the description came up short we wait another beat and retry —
                # up to two retries (so 3 snapshots max).
                await asyncio.sleep(max(nav_delay, 6))
                snap = extract_text_content(await client.call_tool("browser_snapshot", {}, timeout_s=60))
                job = _parse_job_detail(snap, url)
                retries = 0
                while ("description not found" in job.warnings
                       or ("short description" in " ".join(job.warnings) and not job.description)) \
                       and retries < 2:
                    retries += 1
                    log.info("snapshot retry %d for %s (no description yet)", retries, url)
                    await asyncio.sleep(6)
                    snap = extract_text_content(await client.call_tool("browser_snapshot", {}, timeout_s=60))
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
        await client.close()


def scrape_saved_jobs() -> List[SavedJob]:
    """Sync wrapper. Returns all saved jobs from LinkedIn via BrowserMCP."""
    return asyncio.run(extract_saved_jobs())


def save_jobs_json(jobs: List[SavedJob], path: Path) -> Path:
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
]