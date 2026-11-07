"""Unit tests for cursor subscribe display placement."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_bus_store.db.cursor_subscribe_placement import (
    resolve_cursor_display_for_subscribe,
)
from agent_bus_store.db.subscribe_errors import SubscribeError

_TID = "7689373a-2499-41f0-9ff6-fccbcf130362"


def test_display_mismatch_on_vscdb_basis_only() -> None:
    placement = {
        "subscription_display": "orion-node",
        "basis": "vscdb",
        "gui_host": "orion-node",
    }
    with patch(
        "agent_bus_store.db.cursor_subscribe_placement.resolve_cursor_tab_whereami",
        return_value=placement,
    ):
        with pytest.raises(SubscribeError) as exc:
            resolve_cursor_display_for_subscribe(
                display="8",
                agent_id=_TID,
                thread_scope="22502",
            )
    assert exc.value.code == "display_mismatch"


def test_colon_eight_and_eight_agree() -> None:
    placement = {
        "subscription_display": "8",
        "basis": "vscdb",
    }
    with patch(
        "agent_bus_store.db.cursor_subscribe_placement.resolve_cursor_tab_whereami",
        return_value=placement,
    ):
        display, event = resolve_cursor_display_for_subscribe(
            display=":8",
            agent_id=_TID,
            thread_scope="22502",
        )
    assert display == "8"
    assert event is None


def test_display_override_bypasses_mismatch() -> None:
    placement = {
        "subscription_display": "orion-node",
        "basis": "vscdb",
    }
    with patch(
        "agent_bus_store.db.cursor_subscribe_placement.resolve_cursor_tab_whereami",
        return_value=placement,
    ):
        display, event = resolve_cursor_display_for_subscribe(
            display="8",
            agent_id=_TID,
            thread_scope="22502",
            display_override=True,
            display_override_reason="operator confirmed xvfb",
        )
    assert display == "8"
    assert event is not None
    assert event["reason"] == "operator confirmed xvfb"
