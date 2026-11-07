"""MCP agent_bus whereami op."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.subscriptions import _whereami_dispatch


def test_whereami_dispatch_delegates() -> None:
    payload = {
        "gui_host": "orion-node",
        "subscription_display": "orion-node",
        "basis": "vscdb",
    }
    with patch(
        "tools.agent_bus.subscriptions.resolve_cursor_tab_whereami",
        return_value=payload,
    ) as mocked:
        out = _whereami_dispatch(transcript_id="7689373a-3333-4333-8333-333333333333")
    mocked.assert_called_once()
    assert out == payload
