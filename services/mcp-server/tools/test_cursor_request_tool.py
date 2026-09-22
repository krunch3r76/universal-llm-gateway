"""Tests for the narrow ``cursor_request`` MCP tool registration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_vocab import CANONICAL_CONTRACTS, RECORDS
from request_profile import bind_request

from tools.cursor_request import (
    CALLER_FIELDS,
    _dispatch_cursor_request,
    register_cursor_request_tool,
)


class _ToolNameRecorder:
    """Minimal FastMCP duck-type that records tool registration calls."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.functions: dict[str, Any] = {}
        self.kwargs: dict[str, dict[str, Any]] = {}

    def tool(self, **kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered.append(fn.__name__)
            self.functions[fn.__name__] = fn
            self.kwargs[fn.__name__] = kwargs
            return fn

        return decorator


def test_cursor_request_negotiation_descriptor_parity() -> None:
    """Repo-root pytest required (pytest.ini pythonpath)."""
    from services.git_integration_worker.cursor_auto.mission_negotiation_wire import (
        _EXECUTION_FIELDS,
        _PAYLOAD_FIELDS,
        _REQUIRED_FIELDS,
    )

    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)  # type: ignore[arg-type]
    description = recorder.kwargs["cursor_request"].get("description") or ""
    assert "**Mission negotiation:**" in description
    start = description.index("closed 12-field set:")
    end = description.index(".", start)
    enum_run = description[start + len("closed 12-field set:") : end]
    enum_fields = {part.strip() for part in enum_run.split(",") if part.strip()}
    assert enum_fields == set(_REQUIRED_FIELDS)
    assert enum_fields != set(_REQUIRED_FIELDS) - {"parent_thread"}
    payload_start = description.index("six payload fields (") + len("six payload fields (")
    payload_end = description.index(")", payload_start)
    payload_run = description[payload_start:payload_end]
    payload_fields = {part.strip() for part in payload_run.split(",") if part.strip()}
    assert payload_fields == set(_PAYLOAD_FIELDS)
    exec_start = description.index("Execution fields (") + len("Execution fields (")
    exec_end = description.index(")", exec_start)
    exec_run = description[exec_start:exec_end]
    exec_fields = {part.strip() for part in exec_run.split(",") if part.strip()}
    assert exec_fields == set(_EXECUTION_FIELDS)


def test_cursor_request_registers_without_error() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)  # type: ignore[arg-type]
    assert recorder.registered == ["cursor_request", "operator_request"]
    description = recorder.kwargs["cursor_request"].get("description") or ""
    assert description
    for record in RECORDS:
        assert record.name in description
        assert record.closeout_shape in description
    for name in CANONICAL_CONTRACTS:
        assert name in description
    source = (
        Path(__file__)
        .resolve()
        .parent.joinpath("cursor_request.py")
        .read_text(encoding="utf-8")
    )
    assert "live@<sha>" in source
    assert "code_ref_satisfied" in source
    assert "RULING" in source
    assert "Judgment marker (implement admit)" in source


def test_operator_request_forwards_lane_binding_fields() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    operator_request_fn = recorder.functions["operator_request"]
    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"thread": {"id": "901"}, "turn": {"turn_number": 1}}

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            operator_request_fn(
                new_slug="mission",
                subject="Run mission",
                body="TYPE: DIRECTIVE\n",
                parent_thread="700",
                lane_role="operator_proxy",
                request_id="req-1",
            )

    assert captured[0]["parent_thread"] == "700"
    assert captured[0]["lane_role"] == "operator_proxy"
    assert captured[0]["request_id"] == "req-1"


def test_valid_call_delegates_to_request_dispatch_with_to_cursor() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]

    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "thread": {"id": "900"},
            "turn": {"turn_number": 1},
            "auto_handler_status": "auto-handler-live",
            "poll_hint": {"thread": "900", "after_turn": 1},
        }

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            result = cursor_request_fn(
                new_slug="arm-auto",
                subject="Implement X",
                body="TYPE: DIRECTIVE\ncontract: implement\n",
                contract="implement",
                from_agent="web-anthropic",
            )

    assert result["auto_handler_status"] == "auto-handler-live"
    assert len(captured) == 1
    assert captured[0]["to"] == "cursor"
    assert captured[0]["new_slug"] == "arm-auto"
    assert captured[0]["subject"] == "Implement X"
    assert captured[0]["contract"] == "implement"
    assert captured[0]["from_agent"] == "web-anthropic"
    assert "lane" not in captured[0]


def test_cursor_request_forwards_checkout_lane() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]

    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "thread": {"id": "900"},
            "turn": {"turn_number": 1},
            "auto_handler_status": "auto-handler-live",
        }

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            cursor_request_fn(
                new_slug="arm-auto",
                subject="Implement X",
                body="TYPE: DIRECTIVE\ncontract: implement\n",
                contract="implement",
                from_agent="web-anthropic",
                lane="A",
            )

    assert captured[0]["lane"] == "A"


def test_cursor_request_forwards_cse_registration_id():
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]
    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"thread": {"id": "900"}, "turn": {"turn_number": 1}}

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            cursor_request_fn(
                thread="7188",
                subject="Implement X",
                body="TYPE: DIRECTIVE\n",
                from_agent="web-anthropic",
                cse_registration_id="fe05f81fe4f14f8baf75e71050a50650",
            )

    assert captured[0]["cse_registration_id"] == ("fe05f81fe4f14f8baf75e71050a50650")
    assert "cse_registration_id" in CALLER_FIELDS


def test_fastmcp_signatures_include_workspace() -> None:
    import inspect

    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    for name in ("cursor_request", "operator_request"):
        params = inspect.signature(recorder.functions[name]).parameters
        assert "workspace" in params, name
    assert "workspace" in CALLER_FIELDS


def test_cursor_request_forwards_workspace() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]
    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"thread": {"id": "900"}, "turn": {"turn_number": 1}}

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            cursor_request_fn(
                new_slug="ulg-ask-satellite",
                subject="ask: satellite workspace",
                body="Where does this live?",
                from_agent="web-anthropic",
                contract="ask",
                workspace="claudeburst",
            )

    assert captured[0]["workspace"] == "claudeburst"


def test_cursor_request_descriptor_has_ask_first_and_playbook_pointer() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    description = recorder.kwargs["cursor_request"].get("description") or ""
    posture = description.index("Standing seat posture")
    aperture = description.index("Life coding aperture")
    playbook = description.index("document:life-coding-playbook")
    conductor = description.index("Conductor commission")
    assert posture < aperture < playbook < conductor
    assert "agent_skill:conductor" in description
    assert "COMMISSION_CONDUCTOR" not in description
    assert "six-block conductor packet" not in description
    assert "unknown-loci" in description


def test_unknown_caller_argument_rejected_with_accepted_set_error() -> None:
    result = _dispatch_cursor_request(
        {
            "new_slug": "arm-auto",
            "subject": "X",
            "body": "Y",
            "to": "cursor",
        }
    )
    assert "error" in result
    assert "unsupported argument(s): to" in result["error"]
    assert "Accepted:" in result["error"]
    for field in sorted(CALLER_FIELDS):
        assert field in result["error"]


def test_from_omitted_reaches_dispatch_autofilled_on_life_surface() -> None:
    recorder = _ToolNameRecorder()
    register_cursor_request_tool(recorder)
    cursor_request_fn = recorder.functions["cursor_request"]

    captured: list[dict[str, Any]] = []

    def _fake_dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {
            "thread": {"id": "901"},
            "turn": {},
            "auto_handler_status": "no-auto-handler",
        }

    with patch("tools.cursor_request._request_dispatch", side_effect=_fake_dispatch):
        with bind_request("default", surface="life"):
            cursor_request_fn(
                new_slug="auto-fill",
                subject="Probe",
                body="body",
            )

    assert captured[0]["from_agent"] == "web-anthropic"


def test_surface_registration_registers_cursor_request_on_life_only() -> None:
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("surface_registration.py")
        .read_text(encoding="utf-8")
    )
    assert "register_cursor_request_tool" in source
    assert 'if surface == "life":' in source
    life_block = source.split('if surface == "life":', 1)[1]
    assert "register_cursor_request_tool(mcp)" in life_block.split("register_fleet_liveness_tools", 1)[0]


def test_cursor_request_present_on_life_surface_tool_list() -> None:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "cursor_request" in tool_names
    assert "cursor_request" in derive_surface_primary_tools("life")
    cursor_request = next(tool for tool in tools if tool.name == "cursor_request")
    description = cursor_request.description or ""
    assert description, "schema compaction dropped cursor_request description"
    for record in RECORDS:
        assert record.name in description, record.name
        assert record.closeout_shape in description, record.closeout_shape


def test_cursor_request_absent_on_code_surface_tool_list() -> None:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert "cursor_request" not in tool_names
    assert "cursor_request" not in derive_surface_primary_tools("code")
    assert "operator_request" not in tool_names
    assert "operator_request" not in derive_surface_primary_tools("code")
