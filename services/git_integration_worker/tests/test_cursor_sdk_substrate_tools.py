"""Offline unit tests for substrate custom tool builder (AC-2)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from cursor_sdk.types import LocalAgentOptions

from services.git_integration_worker.cursor_sdk_substrate_tools import (
    SubstrateDispatchContext,
    build_substrate_custom_tools,
    merge_substrate_tools,
)

pytestmark = pytest.mark.offline

_CTX = SubstrateDispatchContext(dispatch_id="disp-test", thread_id="6661")


def test_catalog_has_three_read_only_ops_in_rank_order() -> None:
    tools = build_substrate_custom_tools(_CTX)
    assert list(tools.keys()) == [
        "substrate_cortex_read",
        "substrate_bus_tip",
        "substrate_event_read",
    ]


def test_substrate_cortex_read_execute_returns_json() -> None:
    tools = build_substrate_custom_tools(_CTX)
    tool = tools["substrate_cortex_read"]
    with patch(
        "services.git_integration_worker.cursor_sdk_substrate_tools._relay_cortex_entity_get",
        return_value={"entity_id": "todo:x", "name": "X"},
    ):
        out = tool.execute({"entity_id": "todo:x", "intent": "card"}, MagicMock())
    parsed = json.loads(out)
    assert parsed["entity_id"] == "todo:x"


def test_substrate_cortex_read_requires_entity_id() -> None:
    tools = build_substrate_custom_tools(_CTX)
    out = tools["substrate_cortex_read"].execute({}, MagicMock())
    assert json.loads(out)["error"] == "entity_id is required"


def test_substrate_bus_tip_defaults_thread_from_ctx() -> None:
    tools = build_substrate_custom_tools(_CTX)
    with patch(
        "services.git_integration_worker.cursor_sdk_substrate_tools._relay_bus_tip",
        return_value={"thread_id": "6661", "latest_turn": {"turn_number": 9}},
    ) as mock_tip:
        out = tools["substrate_bus_tip"].execute({}, MagicMock())
    mock_tip.assert_called_once_with(thread_id="6661")
    assert json.loads(out)["latest_turn"]["turn_number"] == 9


def test_substrate_event_read_injects_dispatch_id() -> None:
    tools = build_substrate_custom_tools(_CTX)
    with patch(
        "services.git_integration_worker.cursor_sdk_substrate_tools._relay_event_query",
        return_value={"operations": []},
    ) as mock_query:
        tools["substrate_event_read"].execute({"operation": "operations"}, MagicMock())
    call_args = mock_query.call_args[0][0]
    assert call_args["params"]["dispatch_id"] == "disp-test"


def test_merge_substrate_tools_preserves_ambient_local_fields() -> None:
    local = LocalAgentOptions(cwd="/tmp/ws", setting_sources=("all",))
    merged = merge_substrate_tools(local, _CTX)
    assert merged.cwd == local.cwd
    assert list(merged.setting_sources or ()) == ["all"]
    assert "substrate_cortex_read" in (merged.custom_tools or {})


def test_merge_substrate_tools_noop_without_ctx() -> None:
    local = LocalAgentOptions(cwd="/tmp/ws", setting_sources=("all",))
    assert merge_substrate_tools(local, None) is local


def test_build_local_agent_options_unchanged_without_substrate_ctx(
    tmp_path,
) -> None:
    from services.git_integration_worker.cursor_sdk_context import (
        build_local_agent_options,
    )

    ws = tmp_path / "dispatch"
    ws.mkdir()
    opts = build_local_agent_options(ws)
    assert opts.custom_tools is None or opts.custom_tools == {}
