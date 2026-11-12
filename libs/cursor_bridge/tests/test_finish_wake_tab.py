"""finish_wake_tab ledger contract after CDP core envelope."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.offline


def test_finish_wake_tab_success_records_delivered() -> None:
    from cursor_bridge.bridge_glass_relay import finish_wake_tab

    core = {
        "ok": True,
        "reason": "delivered",
        "op": "wake_tab",
        "gui_host": ":8",
        "agent_id": "agent-42",
        "paste_attempted": True,
    }
    with (
        patch("cursor_bridge.bridge_glass_relay.record_tab_wake") as record,
        patch(
            "cursor_bridge.bridge_glass_relay.last_successful_wake", return_value=None
        ),
    ):
        out = finish_wake_tab(core)
    record.assert_called_once_with(
        gui_host=":8",
        agent_id="agent-42",
        op="wake_tab",
        ok=True,
        reason="delivered",
    )
    assert "paste_attempted" not in out


def test_finish_wake_tab_paste_failed_records_failure() -> None:
    from cursor_bridge.bridge_glass_relay import finish_wake_tab

    core = {
        "ok": False,
        "error": "paste_failed",
        "detail": "TimeoutError",
        "op": "wake_tab",
        "gui_host": ":8",
        "agent_id": "agent-42",
        "paste_attempted": True,
    }
    with (
        patch("cursor_bridge.bridge_glass_relay.record_tab_wake") as record,
        patch(
            "cursor_bridge.bridge_glass_relay.last_successful_wake", return_value=None
        ),
    ):
        out = finish_wake_tab(core)
    record.assert_called_once_with(
        gui_host=":8",
        agent_id="agent-42",
        op="wake_tab",
        ok=False,
        reason="paste_failed",
        detail={"detail": "TimeoutError"},
    )
    assert "paste_attempted" not in out


def test_finish_wake_tab_no_page_skips_ledger() -> None:
    from cursor_bridge.bridge_glass_relay import finish_wake_tab

    core = {
        "ok": False,
        "error": "no_page",
        "op": "wake_tab",
        "gui_host": ":8",
        "agent_id": "agent-42",
        "paste_attempted": False,
    }
    with (
        patch("cursor_bridge.bridge_glass_relay.record_tab_wake") as record,
        patch(
            "cursor_bridge.bridge_glass_relay.last_successful_wake", return_value=None
        ),
    ):
        out = finish_wake_tab(core)
    record.assert_not_called()
    assert "paste_attempted" not in out
