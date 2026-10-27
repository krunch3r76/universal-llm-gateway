"""Tests for JSONL → envelope extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from continuity_tape.extract_jsonl import extract_turns_from_jsonl

pytestmark = pytest.mark.offline


def _write_jsonl(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "Hello world."}]},
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hi."},
                    {"type": "tool_use", "name": "grep", "input": {}},
                ]
            },
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def test_extract_turns_from_jsonl_marker_tools(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl)
    envelope = extract_turns_from_jsonl(
        jsonl, tools="marker", session_id="cursor-2026-09-09-120000-abc"
    )
    assert envelope.meta.turn_count == 1
    assert len(envelope.messages) == 2
    assert envelope.messages[1]["content"] == "Hi.\n\n[tool call: grep]"


def test_cursor_marker_send_to_user_has_no_body(tmp_path: Path) -> None:
    jsonl = tmp_path / "send.jsonl"
    records = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": "Ask."}]},
        },
        {
            "role": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "SendToUser",
                        "input": {"text": "secret reply"},
                    }
                ]
            },
        },
    ]
    with jsonl.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    envelope = extract_turns_from_jsonl(jsonl, tools="marker")
    assert envelope.messages[1]["content"] == "[tool call: SendToUser]"


def test_extract_cowork_agent_sdk_jsonl_type_role_and_string_content(
    tmp_path: Path,
) -> None:
    """Cowork SDK JSONL uses top-level type and string message content."""
    jsonl = tmp_path / "cowork.jsonl"
    records = [
        {
            "type": "user",
            "message": {"role": "user", "content": "Plan the task."},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "On it."},
                    {"type": "tool_use", "name": "Write", "input": {"x": 1}},
                ],
            },
        },
    ]
    with jsonl.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    envelope = extract_turns_from_jsonl(
        jsonl, tools="marker", surface="claude-ai", session_id="sess-1"
    )
    assert envelope.meta.surface == "claude_ai"
    assert envelope.meta.turn_count == 1
    assert envelope.messages[0]["content"] == "Plan the task."
    assert envelope.messages[1]["content"] == "On it.\n\n[tool call: Write]"
