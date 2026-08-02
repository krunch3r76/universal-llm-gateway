"""Tests for MCP/stream tool-result unwrapping."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_sdk_tool_result import (
    assertion_id_from_payload,
    unwrap_tool_result,
)

pytestmark = pytest.mark.offline


def test_unwrap_mcp_content_text_array() -> None:
    inner = {"item": {"id": 27489}, "status": "success"}
    result = {"content": [{"type": "text", "text": json.dumps(inner)}]}
    assert unwrap_tool_result(result) == inner
    assert assertion_id_from_payload(unwrap_tool_result(result)) == 27489


def test_unwrap_structured_content_preferred() -> None:
    inner = {"item": {"id": 99}}
    result = {
        "structuredContent": inner,
        "content": [{"type": "text", "text": json.dumps({"item": {"id": 1}})}],
    }
    assert unwrap_tool_result(result) == inner


def test_unwrap_status_success_value_string() -> None:
    inner = {"item": {"id": 12}}
    result = {"status": "success", "value": json.dumps(inner)}
    assert unwrap_tool_result(result) == inner
