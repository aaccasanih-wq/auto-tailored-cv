"""LLM client for any OpenAI-compatible endpoint (defaults: OpenCode Go + GLM 5.2).

OpenCode Go serves models at `https://opencode.ai/zen/go/v1`, fully compatible
with the OpenAI Chat Completions API. The same client works for any provider
that exposes an OpenAI-compatible URL (DeepSeek, OpenRouter, ...). We use the
official `openai` Python SDK to avoid reinventing request/response logic.

This module isolates the LLM call so it can be:
  - invoked by cv_rewriter.py / evaluator.py / repair.py
  - mocked in tests

Only json_mode responses are used because the tailor / evaluate / repair
exchanges need structured outputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from src.config import settings
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    raw: Any

    def as_json(self) -> dict[str, Any]:
        return json.loads(self.content)


class LLMClient:
    """Thin wrapper around the OpenAI SDK pointing at an OpenAI-compatible URL."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        api_key = api_key or settings.llm_api_key
        base_url = base_url or settings.llm_base_url
        if not api_key or api_key == "your-llm-api-key-here":
            raise RuntimeError(
                "LLM_API_KEY is not set. Copy .env.example to .env and paste "
                "your LLM provider API key."
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout or settings.llm_request_timeout,
        )
        self._base_url = base_url

    def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        json_mode: bool = True,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Single-turn chat completion. Returns the first message content."""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        log.debug("LLM call model=%s json_mode=%s", model, json_mode)
        completion = self._client.chat.completions.create(**kwargs)
        content = completion.choices[0].message.content or ""
        log.debug("LLM call returned %d chars", len(content))
        return LLMResponse(content=content, model=model, raw=completion)


def make_client() -> LLMClient:
    """Factory used by run.py to build a client from settings."""
    return LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_request_timeout,
    )


__all__ = ["LLMClient", "LLMResponse", "make_client"]