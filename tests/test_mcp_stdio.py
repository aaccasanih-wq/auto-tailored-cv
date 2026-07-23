"""Tests for src/extract/mcp_stdio.py — pure-Python static helpers only.

The `StdioMcpClient` itself requires a real subprocess so we test only the
pure helpers like `extract_text_content`.
"""

from __future__ import annotations

from src.extract.mcp_stdio import McpError, extract_text_content


class TestExtractTextContent:
    def test_single_text_item(self):
        result = {"content": [{"type": "text", "text": "hello"}]}
        assert extract_text_content(result) == "hello"

    def test_multiple_text_items_joined(self):
        result = {"content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]}
        assert "first" in extract_text_content(result)
        assert "second" in extract_text_content(result)

    def test_skips_non_text_items(self):
        result = {"content": [
            {"type": "image", "url": "data:image/png;"},
            {"type": "text", "text": "ok"},
        ]}
        assert extract_text_content(result) == "ok"

    def test_handles_string_content(self):
        result = {"content": ["a", "b"]}
        assert "a" in extract_text_content(result)
        assert "b" in extract_text_content(result)

    def test_handles_missing_content(self):
        assert extract_text_content({}) == ""
        assert extract_text_content({"content": None}) == ""
        assert extract_text_content({"content": "not a list"}) == ""


class TestMcpError:
    def test_str_no_data(self):
        e = McpError("bad thing happened")
        assert str(e) == "bad thing happened"

    def test_str_with_data(self):
        e = McpError("bad", data={"hint": "x"})
        assert "bad" in str(e)
        assert "x" in str(e)