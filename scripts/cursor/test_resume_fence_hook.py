"""Unit tests for resume_fence_hook.decide()."""

from __future__ import annotations

import json

import pytest

from scripts.cursor.resume_fence_hook import decide

pytestmark = pytest.mark.offline

_MARKER = {
    "fence_id": "rf-deadbeef",
    "root": "10223",
    "state": "poured",
    "read_set": {
        "readable": {
            "shell": False,
            "fs_paths": [],
            "cortex_uris": ["cortex://notes/system/threads/10223-continuity.md"],
            "mcp_allow": [
                {
                    "tool": "continuity",
                    "ops": ["resume", "status", "resume_release"],
                    "thread": "10223",
                },
                {
                    "tool": "agent_bus_read",
                    "ops": ["get", "thread_get"],
                    "thread": "10223",
                },
                {
                    "tool": "fs",
                    "ops": ["read"],
                    "paths": ["cortex://notes/system/threads/10223-continuity.md"],
                },
            ],
        }
    },
}


def test_no_marker_allows_mcp() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": {"tool": "fetch_unread"}},
        marker=None,
        fold=None,
    )
    assert verdict["permission"] == "allow"


def test_fetch_unread_denied_when_poured() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "agent_bus_read",
            "tool_input": json.dumps({"tool": "fetch_unread"}),
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "deny"


def test_continuity_resume_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "continuity",
            "tool_input": {"op": "resume", "thread": "10223"},
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"


def test_fs_card_uri_allowed_foreign_denied() -> None:
    allow = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "fs",
            "tool_input": {
                "op": "read",
                "path": "cortex://notes/system/threads/10223-continuity.md",
            },
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert allow["permission"] == "allow"
    deny = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "fs",
            "tool_input": {
                "op": "read",
                "path": "cortex://notes/system/threads/9796-continuity.md",
            },
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert deny["permission"] == "deny"


def test_released_allows_and_deletes_marker() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": {"tool": "fetch_unread"}},
        marker=_MARKER,
        fold={"state": "released"},
    )
    assert verdict["permission"] == "allow"
    assert verdict.get("delete_marker") is True


def test_exception_fail_closed() -> None:
    bad_marker = {"fence_id": "rf-x", "read_set": "not-a-dict"}
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "fs", "tool_input": {"op": "read", "path": "x"}},
        marker=bad_marker,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "deny"
