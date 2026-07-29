"""Minimal JSON-RPC 2.0 stdio client for talking to MCP servers.

We implement this ourselves instead of installing the `mcp` PyPI package
(which requires Python 3.10+) so the project stays usable on Python 3.9
(the default /usr/bin/python3 on macOS).

MCP-over-stdio conventions:
- Each message is a newline-terminated JSON object (JSON-RPC 2.0).
- Requests from the client include an integer `id`.
- Responses from the server echo back the same `id` with `result` or `error`.
- Server may send notifications (no `id`); we currently ignore them (other
  than logging for debugging).

Implemented methods:
- `initialize` handshake + `notifications/initialized`
- `tools/list`
- `tools/call` with `name` and `arguments`

Limitations:
- Single-threaded per client — concurrent tool calls on the same client are
  intentionally NOT supported; the scraper punctuates its calls sequentially.
- No tool argument validation. The server's response is returned as-is.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)


MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_S = 30
# asyncio StreamReader buffer limit (bytes). The default (64 KiB) is too small
# for Playwright MCP `browser_snapshot` responses: a single JSON-RPC message
# describing a LinkedIn accessibility tree routinely exceeds 1 MiB and can
# reach several MiB. Bumping this avoids
# `ValueError: Separator is found, but chunk is longer than limit`.
DEFAULT_STREAM_LIMIT = 64 * 1024 * 1024  # 64 MiB


@dataclass
class StdioMcpConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    # Working directory for the subprocess; defaults to current.
    cwd: str | None = None


class McpError(Exception):
    def __init__(self, message: str, data: Any | None = None):
        super().__init__(message)
        self.message = message
        self.data = data

    def __str__(self) -> str:
        if self.data:
            return f"{self.message} (data={self.data})"
        return self.message


class StdioMcpClient:
    """Async MCP stdio client. Single in-flight request at a time."""

    def __init__(self, config: StdioMcpConfig, default_timeout_s: int = DEFAULT_TIMEOUT_S,
                 stream_limit: int = DEFAULT_STREAM_LIMIT):
        self.config = config
        self.default_timeout_s = default_timeout_s
        self.stream_limit = stream_limit
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._tools_cache: list[dict[str, Any]] = []
        self._server_info: dict[str, Any] = {}
        self._capabilities: dict[str, Any] = {}

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools_cache

    async def start(self) -> None:
        env = dict(os.environ)
        if self.config.env:
            env.update(self.config.env)
        log.info("starting MCP: %s %s", self.config.command, " ".join(self.config.args))
        self.proc = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            env=env,
            cwd=self.config.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self.stream_limit,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                log.debug("mcp stderr: %s", line.decode("utf-8", errors="replace").rstrip())
        except Exception as e:
            log.debug("stderr drain stopped: %s", e)

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line_text = line.decode("utf-8", errors="replace").strip()
                if not line_text:
                    continue
                try:
                    msg = json.loads(line_text)
                except json.JSONDecodeError:
                    log.debug("ignoring non-JSON MCP line: %s", line_text[:200])
                    continue
                if not isinstance(msg, dict):
                    continue
                # Notifications have no 'id' field
                msg_id = msg.get("id")
                if msg_id is None:
                    log.debug("mcp notification: %s", msg.get("method"))
                    continue
                fut = self._pending.pop(int(msg_id), None)
                if fut is None:
                    continue
                if fut.done():
                    continue
                if "error" in msg:
                    fut.set_exception(McpError(
                        (msg["error"] or {}).get("message", "unknown"),
                        (msg["error"] or {}).get("data"),
                    ))
                else:
                    fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("MCP reader crashed: %s", e)

    async def _send_request(self, method: str, params: Any, timeout_s: int | None = None) -> Any:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("MCP client not started")
        msg_id = self._next_id
        self._next_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params if params is not None else {},
        }
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        line = (json.dumps(request) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()
        return await asyncio.wait_for(fut, timeout=timeout_s or self.default_timeout_s)

    async def _send_notification(self, method: str, params: Any) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("MCP client not started")
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
        }
        line = (json.dumps(notification) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()

    async def initialize(self) -> dict[str, Any]:
        result = await self._send_request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": "auto-tailored-cv",
                "version": "0.1.0",
            },
        })
        if isinstance(result, dict):
            self._capabilities = result.get("capabilities", {}) or {}
            self._server_info = result.get("serverInfo", {}) or {}
        await self._send_notification("notifications/initialized", {})
        log.info(
            "MCP server ready: %s v%s",
            self._server_info.get("name", "?"),
            self._server_info.get("version", "?"),
        )
        return result if isinstance(result, dict) else {}

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._send_request("tools/list", {})
        tools: list[dict[str, Any]] = []
        if isinstance(result, dict):
            tools = result.get("tools", []) or []
        self._tools_cache = tools
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                        timeout_s: int | None = None) -> dict[str, Any]:
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        }, timeout_s=timeout_s)
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()


def extract_text_content(call_result: dict[str, Any]) -> str:
    """Concatenate 'text' portions of a tools/call result.content list."""
    parts: list[str] = []
    content = call_result.get("content", []) or []
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts)


__all__ = [
    "StdioMcpConfig",
    "StdioMcpClient",
    "McpError",
    "extract_text_content",
    "MCP_PROTOCOL_VERSION",
]