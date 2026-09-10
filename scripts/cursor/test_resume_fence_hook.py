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
                {
                    "tool": "cortex",
                    "ops": ["entity_get"],
                    "ids": ["document:10223-continuity"],
                },
                {
                    "tool": "retrieve",
                    "id_prefix": "rs_",
                },
                {
                    "tool": "GetDynamicTools",
                },
            ],
        }
    },
}

_WIRE_GET = json.dumps({"tool": "get", "arguments": '{"thread": 10223}'})
_WIRE_GET_FOREIGN = json.dumps({"tool": "get", "arguments": '{"thread": 9796}'})
_WIRE_FETCH = json.dumps({"tool": "fetch", "arguments": '{"thread": 10223}'})
_WIRE_FS_CARD = json.dumps(
    {
        "tool": "fs",
        "arguments": json.dumps(
            {
                "op": "read",
                "path": "cortex://notes/system/threads/10223-continuity.md",
            }
        ),
    }
)
_WIRE_FS_FOREIGN = json.dumps(
    {
        "tool": "fs",
        "arguments": json.dumps(
            {
                "op": "read",
                "path": "cortex://notes/system/threads/9796-continuity.md",
            }
        ),
    }
)
_WIRE_ENTITY = json.dumps(
    {
        "tool": "entity_get",
        "arguments": json.dumps({"entity_id": "document:10223-continuity"}),
    }
)

_WIRE_FS_DISPATCH = json.dumps(
    {
        "tool": "read",
        "arguments": json.dumps(
            {
                "op": "read",
                "path": "cortex://notes/system/threads/10223-continuity.md",
            }
        ),
    }
)


def test_no_marker_allows_mcp() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": {"tool": "fetch_unread"}},
        marker=None,
        fold=None,
    )
    assert verdict["permission"] == "allow"


def test_fetch_denied_when_poured() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_FETCH},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "deny"


def test_wire_get_allowed_foreign_thread_denied() -> None:
    allow = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_GET},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert allow["permission"] == "allow"
    deny = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_GET_FOREIGN},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert deny["permission"] == "deny"


def test_continuity_resume_allowed_wire_shape() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "continuity",
            "tool_input": json.dumps(
                {
                    "tool": "continuity",
                    "arguments": json.dumps({"op": "resume", "thread": "10223"}),
                }
            ),
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"


def test_fs_card_uri_wire_shape_allowed_foreign_denied() -> None:
    allow = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "fs", "tool_input": _WIRE_FS_CARD},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert allow["permission"] == "allow"
    deny = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "fs", "tool_input": _WIRE_FS_FOREIGN},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert deny["permission"] == "deny"


def test_cortex_entity_get_wire_shape_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "cortex", "tool_input": _WIRE_ENTITY},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"


def test_fs_dispatch_wire_shape_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "fs", "tool_input": _WIRE_FS_DISPATCH},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"


def test_pre_tool_use_grep_denied() -> None:
    verdict = decide(
        event="preToolUse",
        payload={
            "tool_name": "Grep",
            "tool_input": {"pattern": "treasury-scout"},
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "deny"
    assert verdict["journal"]["surface"] == "tool"
    assert verdict["journal"]["tool"] == "Grep"


def test_released_allows_and_deletes_marker() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "agent_bus_read", "tool_input": _WIRE_FETCH},
        marker=_MARKER,
        fold={"state": "released"},
    )
    assert verdict["permission"] == "allow"
    assert verdict.get("delete_marker") is True


def test_retrieve_rs_id_allowed_foreign_id_denied() -> None:
    allow = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "retrieve",
            "tool_input": json.dumps({"id": "rs_34c5e0"}),
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert allow["permission"] == "allow"
    deny = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "retrieve",
            "tool_input": json.dumps({"id": "document:9796-continuity"}),
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert deny["permission"] == "deny"


def test_call_dynamic_tool_retrieve_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={
            "tool_name": "CallDynamicTool",
            "tool_input": json.dumps(
                {
                    "namespace": "project-0-universal-llm-gateway-vortex-code",
                    "toolName": "retrieve",
                    "arguments": {"id": "rs_34c5e0"},
                }
            ),
        },
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"


def test_get_dynamic_tools_allowed() -> None:
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "GetDynamicTools", "tool_input": {}},
        marker=_MARKER,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "allow"


def test_malformed_read_set_hook_error() -> None:
    bad_marker = {"fence_id": "rf-x", "root": "10223", "read_set": "not-a-dict"}
    verdict = decide(
        event="beforeMCPExecution",
        payload={"tool_name": "fs", "tool_input": _WIRE_FS_CARD},
        marker=bad_marker,
        fold={"state": "poured"},
    )
    assert verdict["permission"] == "deny"
    assert verdict["journal"]["reason"] == "hook_error"
