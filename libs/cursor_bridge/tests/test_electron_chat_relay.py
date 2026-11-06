"""Offline unit tests for 1-to-1 Electron paste/read relay (gc1 <-> gc2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.electron_chat_relay import (
    ElectronEndpoint,
    message_fingerprint,
    paste_endpoint_message,
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
    assert (
        should_stop_for_bus_handoff("custom stop", extra_markers=("custom stop",))
        is True
    )


@pytest.mark.asyncio
async def test_read_endpoint_turn_selects_and_reads_latest_prose() -> None:
    gc2 = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Stage Manager (gc2)",
        agent_id="gc2-agent-uuid",
    )
    pages = [
        {
            "title": "Grok Bot",
            "web_socket_debugger_url": "ws://127.0.0.1:9340/devtools/page/1",
        }
    ]
    surfaces = [
        {
            "state": "chat_list",
            "active_title": "Other Chat",
            "active_agent_id": "other-uuid",
            "titles": ["Other Chat", "Stage Manager (gc2)"],
            "title_counts": {"Other Chat": 1, "Stage Manager (gc2)": 1},
            "agent_ids": {
                "other-uuid": "Other Chat",
                "gc2-agent-uuid": "Stage Manager (gc2)",
            },
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
            "agent_ids": {
                "other-uuid": "Other Chat",
                "gc2-agent-uuid": "Stage Manager (gc2)",
            },
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
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_sidebar_target",
            side_effect=_fake_select,
        ),
    ):
        snap = await read_endpoint_turn(gc2, select=True)

    assert snap["ok"] is True
    assert snap["selected"] is True
    assert snap["latest_message"] == "latest turn from gc2"
    assert snap["latest_fingerprint"] == message_fingerprint("latest turn from gc2")


@pytest.mark.asyncio
async def test_run_one_to_one_relay_ping_pongs_until_bus_handoff() -> None:
    gc1 = ElectronEndpoint(
        label="gc1", app="grok", display="9", title="Stage Manager (gc1)"
    )
    gc2 = ElectronEndpoint(
        label="gc2", app="grok", display="10", title="Stage Manager (gc2)"
    )

    state = {
        "gc1": "initial gc1 prose",
        "gc2": "initial gc2 prose",
        "pasted_to_gc2": [],
        "pasted_to_gc1": [],
    }

    async def _fake_read(
        endpoint: ElectronEndpoint, *, select: bool = True
    ) -> dict[str, Any]:
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
        patch(
            "cursor_bridge.electron_chat_relay.read_endpoint_turn",
            side_effect=_fake_read,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.paste_endpoint_message",
            side_effect=_fake_paste,
        ),
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


class _noop_lock:  # noqa: N801 — local async-context stand-in, pre-existing name
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_paste_delivered_requires_semantic_user_turn_not_empty_composer() -> None:
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        agent_id="gc2-agent-uuid",
    )
    select_res = {
        "ok": True,
        "ws_url": "ws://fake",
        "surface": {"composer_draft": "", "generating": False},
        "verdict": {"ok": True, "active_agent_id": "gc2-agent-uuid"},
        "display": "10",
        "app": "grok",
        "selected": True,
    }

    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True}

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {"composer_draft": "", "recent_messages": ["unrelated"]}

    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=select_res,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)

    assert out["ok"] is False
    assert out["code"] == "unknown"


@pytest.mark.asyncio
async def test_paste_matching_draft_is_inserted_not_delivered() -> None:
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        agent_id="gc2-agent-uuid",
    )
    select_res = {
        "ok": True,
        "ws_url": "ws://fake",
        "surface": {"composer_draft": "", "generating": False},
        "verdict": {"ok": True, "active_agent_id": "gc2-agent-uuid"},
        "display": "10",
        "app": "grok",
        "selected": True,
    }

    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True}

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {
            "composer_draft": "exact payload",
            "recent_messages": [],
            "active_agent_id": "gc2-agent-uuid",
            "message_turns": [],
        }

    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=select_res,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)

    assert out["ok"] is False
    assert out["code"] == "send_unconfirmed"
    assert out["pasted"] is True
    assert out["phase"] == "inserted"
    assert out["delivered"] is False


def _grok_select() -> dict:
    return {
        "ok": True,
        "ws_url": "ws://fake",
        "surface": {"composer_draft": "", "generating": False},
        "verdict": {"ok": True, "active_agent_id": "gc2-agent-uuid"},
        "display": "10",
        "app": "grok",
        "selected": True,
    }


@pytest.mark.asyncio
async def test_click_timeout_reconciles_to_submitted_visible_without_second_send() -> (
    None
):
    calls = {"n": 0}

    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        calls["n"] += 1
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "clicked_unknown": True,
            "phase": "send_unknown",
            "pasted": True,
            "inserted": True,
            "retryable": False,
        }

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {
            "composer_draft": "",
            "recent_messages": ["exact payload"],
            "generating": True,
            "active_agent_id": "gc2-agent-uuid",
            "message_turns": [{"role": "user", "text": "exact payload"}],
        }

    ep = ElectronEndpoint(
        label="gc2", app="grok", display="10", agent_id="gc2-agent-uuid"
    )
    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=_grok_select(),
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)
    assert calls["n"] == 1
    assert out["ok"] is True
    assert out["delivered"] is True
    assert out["phase"] == "submitted_visible"
    assert out["generating"] is True
    assert out["phase"] != "acknowledged"
    assert out["clicked_unknown"] is True


@pytest.mark.asyncio
async def test_click_timeout_with_owned_draft_stays_unconfirmed() -> None:
    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "clicked_unknown": True,
            "phase": "send_unknown",
        }

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {
            "composer_draft": "exact payload",
            "recent_messages": [],
            "active_agent_id": "gc2-agent-uuid",
            "message_turns": [],
        }

    ep = ElectronEndpoint(
        label="gc2", app="grok", display="10", agent_id="gc2-agent-uuid"
    )
    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=_grok_select(),
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)
    assert out["ok"] is False
    assert out["code"] == "send_unconfirmed"
    assert out["phase"] == "inserted"
    assert out["delivered"] is False
    assert out["retryable"] is False


@pytest.mark.asyncio
async def test_click_timeout_empty_composer_is_unknown() -> None:
    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": False, "reason": "cdp_eval_timeout", "clicked_unknown": True}

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {
            "composer_draft": "",
            "recent_messages": [],
            "active_agent_id": "gc2-agent-uuid",
            "message_turns": [],
        }

    ep = ElectronEndpoint(
        label="gc2", app="grok", display="10", agent_id="gc2-agent-uuid"
    )
    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=_grok_select(),
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)
    assert out["ok"] is False
    assert out["code"] == "unknown"
    assert out["delivered"] is False
    assert out["phase"] == "send_unknown"


@pytest.mark.asyncio
async def test_preexisting_user_turn_is_not_delivery() -> None:
    select = _grok_select()
    select["surface"] = {
        "composer_draft": "",
        "generating": False,
        "message_turns": [{"role": "user", "text": "exact payload"}],
        "active_agent_id": "gc2-agent-uuid",
    }

    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True, "phase": "send_clicked"}

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {
            "composer_draft": "",
            "active_agent_id": "gc2-agent-uuid",
            "message_turns": [{"role": "user", "text": "exact payload"}],
        }

    ep = ElectronEndpoint(
        label="gc2", app="grok", display="10", agent_id="gc2-agent-uuid"
    )
    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=select,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)
    assert out["ok"] is False
    assert out["delivered"] is False
    assert out["code"] == "unknown"


@pytest.mark.asyncio
async def test_assistant_echo_is_not_delivery() -> None:
    async def _fake_dom_insert(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": True}

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return {
            "composer_draft": "",
            "active_agent_id": "gc2-agent-uuid",
            "generating": True,
            "message_turns": [{"role": "assistant", "text": "exact payload"}],
        }

    ep = ElectronEndpoint(
        label="gc2", app="grok", display="10", agent_id="gc2-agent-uuid"
    )
    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=_grok_select(),
        ),
        patch(
            "cursor_bridge.electron_chat_relay.dom_insert_and_send",
            side_effect=_fake_dom_insert,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            new_callable=AsyncMock,
            side_effect=_fake_probe,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)
    assert out["delivered"] is False
    assert out["ok"] is False
    assert out.get("phase") != "acknowledged"
    assert out["generating"] is True


@pytest.mark.asyncio
async def test_cursor_timer_race_is_unconfirmed_without_readback() -> None:
    async def _fake_cursor(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": "delivery_unconfirmed",
            "pasted": True,
            "delivered": False,
            "race_winner": "timer",
        }

    probe = AsyncMock()
    ep = ElectronEndpoint(label="gc1", app="cursor", display="10", agent_id="gc1-agent")
    select = _grok_select()
    select["app"] = "cursor"
    select["verdict"] = {"ok": True, "active_agent_id": "gc1-agent"}
    with (
        patch(
            "cursor_bridge.electron_chat_relay._resolve_and_select_endpoint",
            new_callable=AsyncMock,
            return_value=select,
        ),
        patch(
            "cursor_bridge.wake_delivery._paste_cursor_services",
            side_effect=_fake_cursor,
        ),
        patch("cursor_bridge.electron_chat_relay.probe_surface", probe),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        out = await paste_endpoint_message(ep, "exact payload", select=True)
    assert out["ok"] is False
    assert out["code"] == "delivery_unconfirmed"
    assert out["delivered"] is False
    assert out["retryable"] is False
    assert out["race_winner"] == "timer"
    probe.assert_not_called()
