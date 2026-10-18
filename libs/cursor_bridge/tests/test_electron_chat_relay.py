"""Offline unit tests for 1-to-1 Electron paste/read relay (gc1 <-> gc2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from cursor_bridge.electron_chat_relay import (
    ElectronEndpoint,
    message_fingerprint,
    read_endpoint_turn,
    run_one_to_one_relay,
    should_stop_for_bus_handoff,
)
from cursor_bridge.wake_delivery import normalize_target

pytestmark = pytest.mark.offline


def test_normalize_target_supports_gc2_on_display_10() -> None:
    target = normalize_target(":10", "grok-bot")
    assert target == {
        "ok": True,
        "display": "10",
        "app": "grok",
        "origin": "http://127.0.0.1:9340",
    }


def test_should_stop_for_bus_handoff_detects_markers() -> None:
    assert should_stop_for_bus_handoff("Normal reply from gc1") is False
    assert should_stop_for_bus_handoff("Ready: HANDOFF_TO_BUS thread=16416") is True
    assert should_stop_for_bus_handoff("BRIDGE_WAKE thread=16416 turn=481") is True
    assert should_stop_for_bus_handoff("custom stop", extra_markers=("custom stop",)) is True


@pytest.mark.asyncio
async def test_read_endpoint_turn_selects_and_reads_latest_prose() -> None:
    gc2 = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Stage Manager (gc2)",
        agent_id="gc2-agent-uuid",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://127.0.0.1:9340/devtools/page/1"}]
    surfaces = [
        {
            "state": "chat_list",
            "active_title": "Other Chat",
            "active_agent_id": "other-uuid",
            "titles": ["Other Chat", "Stage Manager (gc2)"],
            "title_counts": {"Other Chat": 1, "Stage Manager (gc2)": 1},
            "agent_ids": {"other-uuid": "Other Chat", "gc2-agent-uuid": "Stage Manager (gc2)"},
            "composer_draft": "",
            "generating": False,
            "recent_messages": ["old"],
        },
        {
            "state": "chat_list",
            "active_title": "Stage Manager (gc2)",
            "active_agent_id": "gc2-agent-uuid",
            "titles": ["Other Chat", "Stage Manager (gc2)"],
            "title_counts": {"Other Chat": 1, "Stage Manager (gc2)": 1},
            "agent_ids": {"other-uuid": "Other Chat", "gc2-agent-uuid": "Stage Manager (gc2)"},
            "composer_draft": "",
            "generating": False,
            "recent_messages": ["first turn", "latest turn from gc2"],
        },
    ]

    async def _fake_probe(_ws_url: str) -> dict[str, Any]:
        return surfaces.pop(0)

    async def _fake_select(
        _ws_url: str, *, expected_title: str = "", expected_agent_id: str = ""
    ) -> dict[str, Any]:
        assert expected_agent_id == "gc2-agent-uuid"
        assert expected_title == "Stage Manager (gc2)"
        return {"clicked": True}

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe),
        patch("cursor_bridge.electron_chat_relay.select_sidebar_target", side_effect=_fake_select),
    ):
        snap = await read_endpoint_turn(gc2, select=True)

    assert snap["ok"] is True
    assert snap["selected"] is True
    assert snap["latest_message"] == "latest turn from gc2"
    assert snap["latest_fingerprint"] == message_fingerprint("latest turn from gc2")


@pytest.mark.asyncio
async def test_run_one_to_one_relay_ping_pongs_until_bus_handoff() -> None:
    gc1 = ElectronEndpoint(label="gc1", app="grok", display="9", title="Stage Manager (gc1)")
    gc2 = ElectronEndpoint(label="gc2", app="grok", display="10", title="Stage Manager (gc2)")

    state = {
        "gc1": "initial gc1 prose",
        "gc2": "initial gc2 prose",
        "pasted_to_gc2": [],
        "pasted_to_gc1": [],
    }

    async def _fake_read(endpoint: ElectronEndpoint, *, select: bool = True) -> dict[str, Any]:
        del select
        msg = state[endpoint.label]
        return {
            "ok": True,
            "endpoint": endpoint.label,
            "generating": False,
            "latest_message": msg,
            "latest_fingerprint": message_fingerprint(msg),
        }

    async def _fake_paste(
        endpoint: ElectronEndpoint, message: str, *, select: bool = True
    ) -> dict[str, Any]:
        del select
        if endpoint.label == "gc1":
            state["pasted_to_gc1"].append(message)
            # Simulate gc1 replying to the seed or to gc2
            if " SEED " in f" {message} ":
                state["gc1"] = "Hello gc2, let us sync."
            else:
                state["gc1"] = "Confirmed. HANDOFF_TO_BUS thread=16416"
        else:
            state["pasted_to_gc2"].append(message)
            state["gc2"] = "Ack gc1, ready on :10."
        return {
            "ok": True,
            "pasted": True,
            "delivered": True,
            "endpoint": endpoint.label,
            "message": message,
            "fingerprint": message_fingerprint(message),
        }

    with (
        patch("cursor_bridge.electron_chat_relay.read_endpoint_turn", side_effect=_fake_read),
        patch("cursor_bridge.electron_chat_relay.paste_endpoint_message", side_effect=_fake_paste),
    ):
        result = await run_one_to_one_relay(
            gc1,
            gc2,
            seed_message="SEED start",
            max_exchanges=5,
            poll_interval_s=0.01,
            turn_timeout_s=1.0,
        )

    assert result["ok"] is True
    assert result["stopped_reason"] == "bus_handoff"
    assert state["pasted_to_gc2"] == ["Hello gc2, let us sync."]
    assert state["pasted_to_gc1"] == ["SEED start", "Ack gc1, ready on :10."]
    assert result["transcript"][-1]["handoff"] is True
