"""Shared test helpers — stub LLM client that doesn't need an API key."""

from __future__ import annotations

from typing import List

from src.tailor.llm_client import LLMClient, LLMResponse


class StubLLMClient(LLMClient):
    """Returns prepared responses in sequence; bypasses real network calls."""

    def __init__(self, responses: List[LLMResponse]):  # noqa: D401
        # Skip the parent constructor on purpose (no API key needed).
        self._base_url = "stub"
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):  # type: ignore[override]
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("StubLLMClient ran out of canned responses")
        return self._responses.pop(0)


def llm_response(content: str, model: str = "glm-5.2") -> LLMResponse:
    return LLMResponse(content=content, model=model, raw=None)


__all__ = ["StubLLMClient", "llm_response"]