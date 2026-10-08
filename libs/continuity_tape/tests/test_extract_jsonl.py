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
