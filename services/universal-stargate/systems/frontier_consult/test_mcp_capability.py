"""Fork D falsifiers for MCP mechanism vs effective tool access."""

from __future__ import annotations

from agent_seat.profiles import get_profile

from .handoff_response import build_sdk_generate_result
from .mcp_capability import resolve_tool_access


def test_d1_cursor_sdk_reports_local_native_with_tool_access() -> None:
    cap = resolve_tool_access(
        substrate="sdk",
        model="cursor/claude-sonnet-5",
    )
    assert cap.tool_access is True
    assert cap.mcp_mechanism == "local_native"


def test_d3_anthropic_default_remote_connector_mechanism() -> None:
    cap = resolve_tool_access(
        substrate="api",
        model="anthropic/claude-opus-4-8",
    )
    assert cap.mcp_mechanism == "remote_connector"


def test_d3_openai_default_client_side_injection() -> None:
    cap = resolve_tool_access(
        substrate="api",
        model="openai/gpt-5.5",
    )
    assert cap.mcp_mechanism == "client_side_injection"


def test_d2_mechanism_present_without_tool_access_is_representable() -> None:
    cap = resolve_tool_access(
        substrate="sdk",
        model="cursor/claude-sonnet-5",
        suppress_tools=True,
    )
    assert cap.mcp_mechanism == "local_native"
    assert cap.tool_access is False


def test_sdk_generate_result_capabilities_use_tool_access_not_connector_flag() -> None:
    profile = get_profile("cursor", "sdk")
    result = build_sdk_generate_result(
        role="cursor-sdk",
        profile=profile,
        handoff_fields={
            "result_handle": {"kind": "agent_bus_thread", "thread_id": "t1"},
            "handoff_status": "awaiting_first_reply",
            "reply_from_agent": "cursor-sdk",
            "poll_hint": {},
        },
        execution_id="exec-1",
        thread_id="t1",
        to_agent="cursor-sdk",
        resolved_model="cursor/claude-sonnet-5",
        resolved_contract="light-bounded",
        warnings=[],
    )
    caps = result["capabilities"]
    assert caps["tool_access"] is True
    assert caps["mcp_mechanism"] == "local_native"
    assert "mcp_connector_active" not in caps
