"""Tests for JSONL → envelope extraction."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from continuity_tape.extract_jsonl import (
    _walk_turns_legacy,
    extract_turns_from_jsonl,
    extract_turns_from_jsonl_bytes,
)

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MASTER_EXTRACT = "510e1ca6d:libs/continuity_tape/extract_jsonl.py"


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


def _master_walk_turns(
    records: list[dict],
    tmp_path: Path,
    *,
    tools: str = "none",
) -> list[dict[str, str]]:
    """Run ``510e1ca6d`` ``_walk_turns`` on the same records (ground-truth for legacy path)."""
    source = subprocess.check_output(
        ["git", "show", _MASTER_EXTRACT],
        cwd=_REPO_ROOT,
        text=True,
    )
    module_path = tmp_path / "master_extract_jsonl_snapshot.py"
    module_path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("master_extract_jsonl", module_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod._walk_turns(records, tools=tools)  # type: ignore[attr-defined]


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
    """Cowork SDK JSONL uses top-level type and string message content (strict path)."""
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
        jsonl,
        tools="marker",
        surface="claude-ai",
        session_id="sess-1",
        strict_dialogue=True,
    )
    assert envelope.meta.surface == "claude_ai"
    assert envelope.meta.turn_count == 1
    assert envelope.messages[0]["content"] == "Plan the task."
    assert envelope.messages[1]["content"] == "On it.\n\n[tool call: Write]"


def test_default_tools_none_keeps_legacy_walk_without_strict_dialogue(
    tmp_path: Path,
) -> None:
    """Default ``tools=none`` callers match master ``510e1ca6d`` walk output."""
    jsonl = tmp_path / "legacy.jsonl"
    records = [
        {
            "type": "user",
            "isMeta": True,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "meta skill path"}],
            },
        },
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
    with jsonl.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    envelope = extract_turns_from_jsonl(jsonl, tools="none", session_id="legacy")
    master_turns = _master_walk_turns(records, tmp_path, tools="none")
    legacy_turns = _walk_turns_legacy(records, tools="none")
    assert legacy_turns == master_turns
    assert envelope.meta.turn_count == 1
    assert len(envelope.messages) == 2
    assert envelope.messages[0]["content"] == "Hello world."
    assert envelope.messages[1]["content"] == "Hi."


def test_strict_dialogue_opt_in_filters_meta_and_tool_blocks() -> None:
    raw = (
        b'{"type":"system","message":{"role":"system","content":"preamble"}}\n'
        b'{"type":"user","isMeta":true,"message":{"role":"user","content":[{"type":"text","text":"meta"}]}}\n'
        b'{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Hello"}]}}\n'
        b'{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}},{"type":"text","text":"Answer"}]}}\n'
    )
    envelope = extract_turns_from_jsonl_bytes(
        raw,
        tools="none",
        strict_dialogue=True,
        surface="claude_ai",
    )
    assert envelope.meta.turn_count == 1
    assert len(envelope.messages) == 2
    assert envelope.messages[0]["content"] == "Hello"
    assert envelope.messages[1]["content"] == "Answer"


def test_strict_dialogue_two_turns_type_role_and_string_content() -> None:
    """Strict path: type-only roles and string content yield two dialogue turns."""
    raw = (
        b'{"type":"user","message":{"role":"user","content":"First question."}}\n'
        b'{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"First answer."}]}}\n'
        b'{"type":"user","message":{"role":"user","content":"Second question."}}\n'
        b'{"type":"assistant","message":{"role":"assistant","content":"Second answer."}}\n'
    )
    envelope = extract_turns_from_jsonl_bytes(
        raw,
        tools="none",
        strict_dialogue=True,
        surface="claude_ai",
    )
    assert envelope.meta.turn_count == 2
    assert len(envelope.messages) == 4
    contents = [m["content"] for m in envelope.messages]
    assert contents == [
        "First question.",
        "First answer.",
        "Second question.",
        "Second answer.",
    ]
    assert all(c is not None and str(c).strip() for c in contents)


def test_strict_dialogue_skips_is_sidechain_record() -> None:
    raw = (
        b'{"type":"user","isSidechain":true,"message":{"role":"user","content":[{"type":"text","text":"side"}]}}\n'
        b'{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Visible."}]}}\n'
        b'{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Reply."}]}}\n'
    )
    envelope = extract_turns_from_jsonl_bytes(
        raw,
        tools="none",
        strict_dialogue=True,
        surface="claude_ai",
    )
    assert envelope.meta.turn_count == 1
    assert envelope.messages[0]["content"] == "Visible."
    assert envelope.messages[1]["content"] == "Reply."
