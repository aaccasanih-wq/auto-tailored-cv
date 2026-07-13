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


def _extract_heading_h1(snapshot_text: str) -> str:
    """LinkedIn job pages render the job title as a single h1.
    The accessibility-tree text we get back from playwright-style snapshot tools
    looks something like:
        \\ - heading "Senior Data Engineer" [level=1] [ref=e2]
    We look for the first such line. If none, fall back to the <title>-like line."""
    for line in snapshot_text.splitlines():
        if "heading" in line and "level=1" in line:
            m = re.search(r'"([^"]+)"', line)
            if m:
                return m.group(1).strip()
    return ""


def _extract_link_text_after_heading(snapshot_text: str, heading: str) -> str:
    """LinkedIn puts the company name as a link immediately under the title;
    we look for the first link whose text is non-empty after the heading line."""
    seen_heading = False
    for line in snapshot_text.splitlines():
        if "heading" in line and heading and heading.lower() in line.lower():
            seen_heading = True
            continue
        if not seen_heading:
            continue
        if "link" in line:
            m = re.search(r'"([^"]+)"', line)
            if m and m.group(1).strip():
                return m.group(1).strip()
    return ""


def _extract_location(snapshot_text: str) -> str:
    """LinkedIn renders the location as a short text node, usually the first
    generic 'text' node immediately after the company link. As a heuristic we
    find a line containing '·' OR matching the pattern 'City, Country'."""
    for line in snapshot_text.splitlines():
        t = line.strip().lstrip("- ").strip()
        if t.startswith("text ") and ("·" in t or "," in t or "Ciudad" in t):
            m = re.search(r'"([^"]+)"', t)
            if m and m.group(1).strip():
                # Filter false positives (long sentences)
                value = m.group(1).strip()
                if len(value) <= 80:
                    return value
    return ""


def _extract_description(snapshot_text: str) -> str:
    """LinkedIn job descriptions live in a long block — usually the largest
    concentrated text after the 'About the job' marker (English) or
    'Acerca del empleo' (Spanish)."""
    marker_phrase = "Acerca del empleo"
    snippet = snapshot_text
    if marker_phrase.lower() in snapshot_text.lower():
        idx = snapshot_text.lower().find(marker_phrase.lower())
        snippet = snapshot_text[idx + len(marker_phrase):]
    else:
        # Fallback: capture everything after the second heading (after title)
        headings = [l for l in snapshot_text.splitlines() if "heading" in l]
        if len(headings) >= 2:
            idx = snapshot_text.find(headings[1])
            snippet = snapshot_text[idx + len(headings[1]):]

    # Extract the text lines after the marker; we keep those that look like
    # plain content (starting with text/paragraph) until we hit a section
    # header like "Ver empleos similares" / "Solicitar".
    description_lines: List[str] = []
    stop_phrases = (
        "Ver empleos similares",
        "Solicitar",
        "Aplicar",
        "See similar jobs",
        "Apply",
        "About us",
        "Acerca de nosotros",
    )
    for line in snippet.splitlines():
        t = line.strip()
        if not t:
            continue
        # Skip snapshot tool-ref and modifier brackets.
        if "[ref=" in t or "level=" in t or "expanded=" in t:
            continue
        # Strip leading list dashes and quotes.
        text = re.sub(r'^[\s\-]+', '', t)
        text = re.sub(r'^text\s+', '', text)
        text = text.strip(' "\'')
        if not text:
            continue
        if any(stop in text.lower() for stop in (p.lower() for p in stop_phrases)):
            break
        description_lines.append(text)

    return "\n".join(description_lines).strip()


def _parse_job_detail(snapshot_text: str, url: str) -> SavedJob:
    title = _extract_heading_h1(snapshot_text)
    company = _extract_link_text_after_heading(snapshot_text, title) if title else ""
    location = _extract_location(snapshot_text)
    description = _extract_description(snapshot_text)

    job_id = ""
    m = JOB_URL_RE.search(url) if url else None
    if m:
        job_id = m.group(1) or m.group(2) or ""

    warnings: List[str] = []
    if not title:
        warnings.append("title not found")
    if not description:
        warnings.append("description not found")

    return SavedJob(
        title=title,
        url=url,
        company=company,
        location=location,
        description=description,
        job_id=job_id,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


def _build_mcp_config() -> StdioMcpConfig:
    return StdioMcpConfig(
        command=settings.browser_mcp_command,
        args=list(settings.browser_mcp_args),
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
        await client.call_tool("browser_navigate", {"url": saved_jobs_url})
        await asyncio.sleep(nav_delay)
        list_snapshot = extract_text_content(await client.call_tool("browser_snapshot", {}))
        job_urls = _extract_job_urls(list_snapshot)
        log.info("found %d saved-job URLs in snapshot", len(job_urls))

        # 2. Visit each job page and parse.
        jobs: List[SavedJob] = []
        for idx, url in enumerate(job_urls, start=1):
            log.info("[%d/%d] navigating to %s", idx, len(job_urls), url)
            try:
                await client.call_tool("browser_navigate", {"url": url})
                await asyncio.sleep(nav_delay)
                snap = extract_text_content(await client.call_tool("browser_snapshot", {}))
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