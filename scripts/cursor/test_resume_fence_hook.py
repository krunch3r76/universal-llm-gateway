"""Unit tests for resume_fence_hook — undenied substrate, bleed nudge only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.cursor.resume_fence_hook import decide, handle_event

pytestmark = pytest.mark.offline

_ARMED = {
    "fence_id": "rf-deadbeef",
    "root": "11738",
    "state": "armed",
    "transcript_id": "84ec9c52-99e3-4e2b-af35-8a56949c6498",
}

_WIRE_TAPE = json.dumps(
    {"tool": "tape", "arguments": '{"thread": 11738, "harvest": true}'}
)
_WIRE_GET = json.dumps({"tool": "get", "arguments": '{"thread": 11738}'})
_WIRE_CORTEX = json.dumps(
    {"tool": "search", "arguments": '{"query": "music-lexicon-accord"}'}
)


def test_no_marker_allows() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_TAPE},
        marker=None,
        fold=None,
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" not in verdict


def test_armed_tape_allowed_no_nudge() -> None:
    """84ec9c52: pour is tape — hook must not deny or scold it."""
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_TAPE},
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" not in verdict


def test_armed_pretool_tape_prefixed_allowed() -> None:
    verdict = decide(
        event="preToolUse",
        payload={
            "tool_name": "MCP:agent_bus_read",
            "tool_input": {"tool": "tape", "arguments": {"thread": "11738"}},
        },
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" not in verdict


def test_armed_cortex_search_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "cortex", "tool_input": _WIRE_CORTEX},
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" not in verdict


def test_armed_rename_chat_allowed() -> None:
    verdict = decide(
        event="preToolUse",
        payload={
            "tool_name": "MCP:rename_chat",
            "tool_input": {"title": "11738 music-lexicon-accord"},
        },
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" not in verdict


def test_armed_read_overflow_allowed() -> None:
    verdict = decide(
        event="preToolUse",
        payload={
            "tool_name": "Read",
            "tool_input": {
                "path": "/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-tools/overflow.txt"
            },
        },
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"


def test_armed_get_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_GET},
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"


def test_armed_grep_nudges_but_allows() -> None:
    verdict = decide(
        event="preToolUse",
        payload={"tool_name": "Grep", "tool_input": {"pattern": "deaf ear"}},
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"
    assert "continuity(op=resume" in verdict["agent_message"]
    assert verdict["journal"]["reason"] == "bleed_nudge"
    assert verdict["journal"]["tool"] == "Grep"


def test_armed_get_dynamic_tools_nudges_but_allows() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "GetDynamicTools", "tool_input": {}},
        marker=_ARMED,
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" in verdict
    assert verdict["journal"]["reason"] == "bleed_nudge"


def test_poured_grep_silent_allow() -> None:
    verdict = decide(
        event="preToolUse",
        payload={"tool_name": "Grep", "tool_input": {"pattern": "x"}},
        marker={**_ARMED, "state": "poured"},
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"
    assert "agent_message" not in verdict


def test_released_allows_and_deletes_marker() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_TAPE},
        marker=_ARMED,
        fold={"state": "released"},
    )
    assert verdict["permission"] == "allow"
    assert verdict.get("delete_marker") is True


def test_malformed_marker_still_allows() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "fs", "tool_input": {}},
        marker={"fence_id": "rf-x", "read_set": "not-a-dict"},
        fold={"state": "armed"},
    )
    assert verdict["permission"] == "allow"


def test_session_start_clears_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.cursor.resume_fence_hook as hook

    monkeypatch.setattr(hook, "_MARKER_DIR", tmp_path)
    cid = "84ec9c52-99e3-4e2b-af35-8a56949c6498"
    marker = tmp_path / f"{cid}.json"
    marker.write_text(json.dumps(_ARMED))
    result = handle_event("sessionStart", {"conversation_id": cid})
    assert result == {}
    assert not marker.exists()
