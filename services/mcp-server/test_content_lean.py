"""Tests for JSON-mirror content minification on passthrough (lane A)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp.tools.tool import ToolResult
from mcp.types import ImageContent, TextContent
from request_profile import bind_profile, bind_request, current_structured_capable
from response_size_guard import (
    _CONTENT_LEAN_FLOOR_BYTES,
    _CONTENT_LEAN_TOOLS,
    _MIRROR_PLACEHOLDER,
    _MIRROR_SUPPRESS_TOOLS,
    _lean_content,
    _measure_result,
    _reasoning_target_bytes,
    _threshold_for_profile,
)


def _mirror_result(payload: Any, *, pretty: bool = True) -> ToolResult:
    if pretty:
        text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )


def _whitespace_heavy_payload() -> dict[str, Any]:
    return {
        "items": [{"id": f"n{i}", "name": f"Name {i}"} for i in range(50)],
        "valid_until": None,
    }


def test_mirror_minifies_with_semantic_identity() -> None:
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    old_text = original.content[0].text
    leaned = _lean_content(original, tool_name="cortex")
    new_text = leaned.content[0].text
    assert json.loads(new_text) == json.loads(old_text)
    assert json.loads(new_text)["valid_until"] is None
    old_bytes = len(old_text.encode("utf-8"))
    new_bytes = len(new_text.encode("utf-8"))
    assert new_bytes < old_bytes
    assert (1 - new_bytes / old_bytes) >= 0.15


def test_structured_content_byte_identical_and_untouched() -> None:
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    before = json.dumps(original.structured_content, sort_keys=True).encode("utf-8")
    sc_id = id(original.structured_content)
    leaned = _lean_content(original, tool_name="cortex")
    after = json.dumps(leaned.structured_content, sort_keys=True).encode("utf-8")
    assert before == after
    assert leaned.structured_content is original.structured_content
    assert id(leaned.structured_content) == sc_id


def test_strictly_smaller_passthrough_for_compact_content() -> None:
    payload = {"a": 1}
    original = _mirror_result(payload, pretty=False)
    text = original.content[0].text
    assert _lean_content(original, tool_name="cortex") is original
    assert original.content[0].text == text


def test_multi_block_unchanged() -> None:
    payload = _whitespace_heavy_payload()
    text = json.dumps(payload, indent=2)
    original = ToolResult(
        content=[
            TextContent(type="text", text=text),
            TextContent(type="text", text='{"extra": true}'),
        ],
        structured_content=payload,
    )
    assert _lean_content(original, tool_name="cortex") is original


def test_image_content_unchanged() -> None:
    payload = _whitespace_heavy_payload()
    text = json.dumps(payload, indent=2)
    original = ToolResult(
        content=[
            TextContent(type="text", text=text),
            ImageContent(type="image", data="aGVsbG8=", mimeType="image/png"),
        ],
        structured_content=payload,
    )
    assert _lean_content(original, tool_name="cortex") is original


def test_below_floor_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "response_size_guard._CONTENT_LEAN_FLOOR_BYTES",
        10_000,
    )
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    assert _lean_content(original, tool_name="cortex") is original


def test_human_note_non_mirror_unchanged() -> None:
    manifest = {"large_payload": True, "ref_id": "rs_abc123"}
    original = ToolResult(
        content=[TextContent(type="text", text="Large payload flagged.\nUse retrieve.")],
        structured_content=manifest,
    )
    assert _lean_content(original, tool_name="agent_bus") is original


def test_fs_raw_json_inner_text_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("response_size_guard._CONTENT_LEAN_FLOOR_BYTES", 100)
    inner = '{\n  "version": 1,\n  "name": "config"\n}'
    payload = {
        "path": "cfg.json",
        "content": inner,
        "size": len(inner),
        "items": [{"id": f"f{i}", "note": f"row {i}"} for i in range(60)],
    }
    original = _mirror_result(payload)
    leaned = _lean_content(original, tool_name="fs")
    assert json.loads(leaned.content[0].text)["content"] == inner
    assert json.loads(leaned.content[0].text) == json.loads(original.content[0].text)


def test_non_gated_tool_unchanged() -> None:
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    assert _lean_content(original, tool_name="retrieve") is original


def test_exception_returns_original() -> None:
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)

    class BadText:
        type = "text"

        @property
        def text(self) -> str:
            raise RuntimeError("boom")

    broken = original.model_copy(update={"content": [BadText()]})
    with patch("response_size_guard.record") as mock_record:
        out = _lean_content(broken, tool_name="cortex")
    assert out is broken
    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "mcp.response.lean.error"


def test_success_event_carries_counts_not_bodies() -> None:
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    events: list[tuple[str, dict[str, Any]]] = []

    def capture(name: str, **kwargs: Any) -> None:
        events.append((name, kwargs))

    with patch("response_size_guard.record", side_effect=capture):
        _lean_content(original, tool_name="cortex")
    assert events
    event_name, fields = events[0]
    assert event_name == "mcp.response.leaned"
    assert "old_bytes" in fields
    assert "new_bytes" in fields
    assert "reduction_ratio" in fields
    assert fields["tool"] == "cortex"
    assert "text" not in fields
    assert "content" not in fields


def test_content_lean_tools_cover_spec_set() -> None:
    assert _CONTENT_LEAN_TOOLS == frozenset({"cortex", "agent_bus", "fs", "rag"})


def test_default_floor_is_2048() -> None:
    assert _CONTENT_LEAN_FLOOR_BYTES == 2048


def test_current_structured_capable_default_false() -> None:
    assert current_structured_capable() is False


def test_current_structured_capable_true_inside_bind_request() -> None:
    with bind_request("default", structured_capable=True):
        assert current_structured_capable() is True
    assert current_structured_capable() is False


def test_bind_profile_leaves_structured_capable_false() -> None:
    with bind_profile("default"):
        assert current_structured_capable() is False


def test_mirror_suppress_tools_subset_of_content_lean_tools() -> None:
    assert _MIRROR_SUPPRESS_TOOLS <= _CONTENT_LEAN_TOOLS


def test_mirror_suppressed_when_capable_and_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("response_size_guard._CONTENT_LEAN_FLOOR_BYTES", 100)
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    pre_bytes = _measure_result(original)
    with bind_request("default", structured_capable=True):
        suppressed = _lean_content(original, tool_name="cortex")
    assert suppressed.content[0].text == _MIRROR_PLACEHOLDER
    assert len(suppressed.content) == 1
    assert json.loads(_MIRROR_PLACEHOLDER)
    assert suppressed.structured_content is original.structured_content
    post_bytes = _measure_result(suppressed)
    target = _reasoning_target_bytes(_threshold_for_profile())
    assert post_bytes < pre_bytes
    assert post_bytes < target
    assert post_bytes < pre_bytes * 0.75


def test_no_suppress_when_not_structured_capable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("response_size_guard._CONTENT_LEAN_FLOOR_BYTES", 100)
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    leaned = _lean_content(original, tool_name="cortex")
    assert leaned.content[0].text != _MIRROR_PLACEHOLDER
    assert json.loads(leaned.content[0].text) == json.loads(original.content[0].text)


def test_no_suppress_on_non_mirror_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("response_size_guard._CONTENT_LEAN_FLOOR_BYTES", 100)
    manifest = {"large_payload": True, "ref_id": "rs_abc123"}
    original = ToolResult(
        content=[TextContent(type="text", text="Large payload flagged.\nUse retrieve.")],
        structured_content=manifest,
    )
    with bind_request("default", structured_capable=True):
        out = _lean_content(original, tool_name="agent_bus")
    assert out is original


def test_mirror_suppress_event_carries_counts_not_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("response_size_guard._CONTENT_LEAN_FLOOR_BYTES", 100)
    payload = _whitespace_heavy_payload()
    original = _mirror_result(payload)
    events: list[tuple[str, dict[str, Any]]] = []

    def capture(name: str, **kwargs: Any) -> None:
        events.append((name, kwargs))

    with bind_request("default", structured_capable=True):
        with patch("response_size_guard.record", side_effect=capture):
            _lean_content(original, tool_name="cortex")
    assert events
    event_name, fields = events[0]
    assert event_name == "mcp.response.mirror_suppressed"
    assert fields["tool"] == "cortex"
    assert "wire_bytes" in fields
    assert "text" not in fields
    assert "content" not in fields
