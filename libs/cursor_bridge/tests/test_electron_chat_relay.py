"""Offline unit tests for 1-to-1 Electron paste/read relay (gc1 <-> gc2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.chat_surface import wake_decision as _real_wake_decision
from cursor_bridge.display_interaction_locks import (
    _DISPLAY_SWITCH_DEPTH,
    _INPROC_SWITCH,
)
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
async def test_read_grok_stale_conversation_after_select_not_prior_messages() -> None:
    """Break: prior chat lines visible after CDP select must not be returned as read."""
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Fresh Bot",
        agent_id="new-agent-id",
    )
    stale_line = "stale transcript line from previous chat"
    surfaces = [
        {
            "state": "chat_list",
            "active_title": "Other Chat",
            "active_agent_id": "other-uuid",
            "titles": ["Other Chat", "Fresh Bot"],
            "composer_draft": "",
            "generating": False,
            "recent_messages": [stale_line],
        },
        {
            "state": "chat_list",
            "active_title": "Fresh Bot",
            "active_agent_id": "new-agent-id",
            "titles": ["Other Chat", "Fresh Bot"],
            "composer_draft": "",
            "generating": False,
            "recent_messages": [stale_line],
        },
    ]
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        if len(surfaces) > 1:
            return dict(surfaces.pop(0))
        return dict(surfaces[0])

    async def _fake_select_grok(_display: object, _agent_id: str) -> dict[str, Any]:
        return {"ok": True, "currentAgentId": "new-agent-id"}

    stale_surface = surfaces[-1]
    stale_verdict = {
        "ok": True,
        "active_agent_id": "new-agent-id",
        "active_title": "Fresh Bot",
    }

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            side_effect=_fake_select_grok,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_grok_after_select",
            new_callable=AsyncMock,
            return_value={
                "ok": False,
                "stale_dom": True,
                "code": "conversation_unconfirmed",
                "surface": stale_surface,
                "verdict": stale_verdict,
            },
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is False
    assert snap["code"] == "conversation_unconfirmed"
    assert stale_line not in (snap.get("recent_messages") or [])


@pytest.mark.asyncio
async def test_grok_select_reprobes_surface_inside_switch_lock_for_prior_generating() -> (
    None
):
    """Break: pre-lock idle snapshot must not be the C2 baseline after a concurrent wake."""
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Target Bot",
        agent_id="target-agent-id",
    )
    idle = {
        "state": "chat_list",
        "active_title": "Other Chat",
        "active_agent_id": "other-uuid",
        "titles": ["Other Chat", "Target Bot"],
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["baseline"],
    }
    streaming = {
        **idle,
        "generating": True,
        "recent_messages": ["baseline", "x-stream-chunk"],
    }
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    probes = [dict(idle), dict(streaming)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(streaming)

    grok_after = AsyncMock(
        return_value={
            "ok": True,
            "stale_dom": False,
            "surface": streaming,
            "verdict": {
                "ok": True,
                "active_agent_id": "target-agent-id",
                "active_title": "Target Bot",
            },
        }
    )

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "target-agent-id"},
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_grok_after_select",
            grok_after,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    grok_after.assert_awaited_once()
    assert grok_after.await_args.kwargs["prior_generating"] is True


def _raw_probe(
    *,
    active_agent_id: str,
    active_title: str,
    titles: list[str],
    recent_messages: list[str],
    generating: bool = False,
    has_active_chat: bool | None = None,
    agent_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    agent_ids = agent_ids or {active_agent_id: active_title}
    items = [
        {
            "text": t,
            "selected": aid == active_agent_id,
            "agent_id": aid,
        }
        for aid, t in agent_ids.items()
    ]
    return {
        "labels": [],
        "items": items,
        "composer_draft": "",
        "generating": generating,
        "recent_messages": recent_messages,
        "message_turns": [],
        "has_active_chat": has_active_chat,
    }


def _switch_lock_held(display: str) -> bool:
    import asyncio

    loop = asyncio.get_running_loop()
    per_loop = _INPROC_SWITCH.get(loop)
    if not per_loop:
        return False
    entry = per_loop.get(str(display))
    if entry is None:
        return False
    return entry.lock.locked()


@pytest.mark.asyncio
async def test_read_grok_select_while_other_bot_streaming_attributes_with_real_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    baseline = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=["shared stale line"],
        generating=True,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    mid = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["shared stale line"],
        generating=True,
        has_active_chat=False,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    final = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["prose from bot y"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    probes = [dict(baseline), dict(baseline), dict(mid), dict(final)]

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        if probes:
            return probes.pop(0)
        return dict(final)

    async def _rec_sleep(_delay: float) -> None:
        return None

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "bot-y"},
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    assert snap["active_agent_id"] == "bot-y"
    assert snap["latest_message"] == "prose from bot y"


@pytest.mark.asyncio
async def test_read_grok_peer_switch_restore_during_unlocked_wait_unconfirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    shared = "shared stale line"
    pre = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=[shared],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    in_lock = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=[shared],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    post_select_bot_y = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=[shared],
        generating=False,
        has_active_chat=False,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    wait_z = _raw_probe(
        active_agent_id="bot-z",
        active_title="Bot Z",
        titles=["Bot X", "Bot Y", "Bot Z"],
        recent_messages=[shared],
        generating=False,
        has_active_chat=False,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y", "bot-z": "Bot Z"},
    )
    misattrib = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["z line"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    probes = [
        dict(pre),
        dict(in_lock),
        dict(post_select_bot_y),
        dict(wait_z),
        dict(misattrib),
    ]

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        return probes.pop(0) if probes else dict(misattrib)

    async def _rec_sleep(_delay: float) -> None:
        return None

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "bot-y"},
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is False
    assert snap["code"] == "conversation_unconfirmed"
    assert snap.get("foreign_agent_id") == "bot-z"
    assert "z line" not in (snap.get("recent_messages") or [])
    assert snap.get("latest_message", "") != "z line"


@pytest.mark.asyncio
async def test_read_grok_in_lock_busy_guard_skips_cdp_select(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Break: concurrent steering while switch lock is held must not CDP-select."""
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    pre = {
        "state": "chat_list",
        "active_title": "Bot X",
        "active_agent_id": "bot-x",
        "titles": ["Bot X", "Bot Y"],
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["idle"],
    }
    in_lock = {
        **pre,
        "composer_draft": "please hold",
        "generating": True,
    }
    probes = [dict(pre), dict(in_lock)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(in_lock)

    select_mock = AsyncMock(return_value={"ok": True, "currentAgentId": "bot-y"})

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            select_mock,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is False
    assert snap["reason"] == "composer_not_empty"
    select_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_grok_post_wait_busy_guard_skips_reselect_click(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Break: post-wait re-select path must not sidebar-click under composer guard."""
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    pre = {
        "state": "chat_list",
        "active_title": "Bot X",
        "active_agent_id": "bot-x",
        "titles": ["Bot X", "Bot Y"],
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["idle"],
    }
    in_lock = dict(pre)
    post_wait = {
        **pre,
        "composer_draft": "please hold",
        "generating": True,
    }
    probes = [dict(pre), dict(in_lock), dict(post_wait)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(post_wait)

    select_grok_mock = AsyncMock(return_value={"ok": True, "currentAgentId": "bot-y"})
    sidebar_mock = AsyncMock(return_value={"clicked": True})
    not_ok_verdict = {
        "ok": False,
        "state": "chat_list",
        "active_agent_id": "bot-x",
        "active_title": "Bot X",
        "expected_agent_id": "bot-y",
        "expected_title": "Bot Y",
    }

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            select_grok_mock,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_grok_after_select",
            new_callable=AsyncMock,
            return_value={
                "ok": False,
                "stale_dom": False,
                "surface": dict(pre),
                "verdict": dict(not_ok_verdict),
            },
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_sidebar_target",
            sidebar_mock,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is False
    assert snap["reason"] == "composer_not_empty"
    select_grok_mock.assert_awaited_once()
    sidebar_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_grok_select_real_wait_stale_pane_unconfirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    stale = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=["never binds"],
        generating=True,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    still_stale = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["never binds"],
        generating=True,
        has_active_chat=False,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    probes = [dict(stale), dict(stale), dict(still_stale)]

    wait_queue = [dict(still_stale)]

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        if probes:
            return probes.pop(0)
        return wait_queue[0]

    async def _rec_sleep(_delay: float) -> None:
        return None

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "bot-y"},
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is False
    assert snap["code"] == "conversation_unconfirmed"


@pytest.mark.asyncio
async def test_display_switch_lock_not_held_during_probe_sleeps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    baseline = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=["line"],
        generating=True,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    mid = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["line"],
        generating=True,
        has_active_chat=False,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    final = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["y line"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    probes = [dict(baseline), dict(baseline), dict(mid), dict(final)]

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        return probes.pop(0) if probes else dict(final)

    holds_during_sleep: list[bool] = []

    async def _rec_sleep(_delay: float) -> None:
        holds_during_sleep.append(_switch_lock_held("10"))

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "bot-y"},
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    assert holds_during_sleep
    assert not any(holds_during_sleep)


@pytest.mark.asyncio
async def test_reselect_reacquires_display_switch_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    baseline = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=["line"],
        generating=True,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    mid_wait = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["line"],
        generating=True,
        has_active_chat=False,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    wait_end = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["y fresh"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    after_click = dict(wait_end)
    probes = [
        dict(baseline),
        dict(baseline),
        dict(mid_wait),
        dict(wait_end),
        dict(after_click),
    ]
    depths_at_select: list[int] = []
    wake_calls = 0

    def _wake(surface: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        nonlocal wake_calls
        verdict = _real_wake_decision(surface, **kwargs)
        wake_calls += 1
        if wake_calls >= 2:
            return {**verdict, "ok": False, "reason": "synthetic_wake_miss"}
        return verdict

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        return probes.pop(0) if probes else dict(after_click)

    async def _rec_sleep(_delay: float) -> None:
        return None

    async def _select_sidebar(*_a: Any, **_k: Any) -> dict[str, Any]:
        depths_at_select.append(_DISPLAY_SWITCH_DEPTH.get())
        return {"clicked": True}

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "bot-y"},
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_sidebar_target",
            side_effect=_select_sidebar,
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
        patch("cursor_bridge.electron_chat_grok_wait.wake_decision", side_effect=_wake),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    assert depths_at_select == [1]


@pytest.mark.asyncio
async def test_read_grok_honest_lag_baseline_prose_then_target_attributes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """F-J: post-select honest lag on baseline must still attribute once target prose is fresh."""
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    shared = "shared line"
    in_lock = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=[shared],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    honest_lag = dict(in_lock)
    final = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["y fresh"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    probes = [dict(in_lock), dict(in_lock), dict(honest_lag), dict(final)]

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        return probes.pop(0) if probes else dict(final)

    async def _rec_sleep(_delay: float) -> None:
        return None

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "bot-y"},
        ),
        patch(
            "cursor_bridge.electron_chat_grok_wait.asyncio.sleep",
            side_effect=_rec_sleep,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    assert snap["latest_message"] == "y fresh"


@pytest.mark.asyncio
async def test_read_grok_cdp_select_fail_verdict_ok_skips_sidebar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """F-K: failed roster CDP select with in-lock wake ok must not sidebar-click."""
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    pre_select = {
        "state": "chat_list",
        "active_title": "Bot X",
        "active_agent_id": "bot-x",
        "titles": ["Bot X", "Bot Y"],
        "title_counts": {"Bot X": 1, "Bot Y": 1},
        "agent_ids": {"bot-x": "Bot X", "bot-y": "Bot Y"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["pre select"],
        "has_active_chat": True,
    }
    in_lock_ok = {
        "state": "chat_list",
        "active_title": "Bot Y",
        "active_agent_id": "bot-y",
        "titles": ["Bot X", "Bot Y"],
        "title_counts": {"Bot X": 1, "Bot Y": 1},
        "agent_ids": {"bot-x": "Bot X", "bot-y": "Bot Y"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["already on y"],
        "has_active_chat": True,
    }
    probes = [dict(pre_select), dict(in_lock_ok)]

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else dict(in_lock_ok)

    sidebar = AsyncMock(return_value={"clicked": True})

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface",
            side_effect=_fake_probe,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": False, "reason": "cdp_fail"},
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_sidebar_target",
            sidebar,
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    assert snap["active_agent_id"] == "bot-y"
    sidebar.assert_not_awaited()


@pytest.mark.asyncio
async def test_grok_cdp_select_failure_falls_back_to_sidebar_click(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Break: failed roster CDP select must still sidebar-click under display_switch_lock."""
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    monkeypatch.setenv("ULG_DISPLAY_LOCK_DIR", str(tmp_path))
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Bot Y",
        agent_id="bot-y",
    )
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    baseline = _raw_probe(
        active_agent_id="bot-x",
        active_title="Bot X",
        titles=["Bot X", "Bot Y"],
        recent_messages=["line"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    after_click = _raw_probe(
        active_agent_id="bot-y",
        active_title="Bot Y",
        titles=["Bot X", "Bot Y"],
        recent_messages=["y prose"],
        generating=False,
        has_active_chat=True,
        agent_ids={"bot-x": "Bot X", "bot-y": "Bot Y"},
    )
    probes = [dict(baseline), dict(baseline), dict(after_click)]
    depths_at_sidebar: list[int] = []

    async def _eval(_ws: str, expression: str, **_: Any) -> Any:
        if "sand-message-prose" not in expression:
            return {"labels": [], "items": []}
        return probes.pop(0) if probes else dict(after_click)

    async def _select_sidebar(*_a: Any, **_k: Any) -> dict[str, Any]:
        depths_at_sidebar.append(_DISPLAY_SWITCH_DEPTH.get())
        return {"clicked": True}

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": False, "reason": "cdp_select_failed"},
        ) as cdp_select,
        patch(
            "cursor_bridge.electron_chat_relay.select_sidebar_target",
            side_effect=_select_sidebar,
        ) as sidebar,
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    cdp_select.assert_awaited_once()
    sidebar.assert_awaited_once()
    assert depths_at_sidebar == [1]
    assert snap["ok"] is True
    assert snap["active_agent_id"] == "bot-y"


@pytest.mark.asyncio
async def test_read_grok_empty_dom_ok_after_select() -> None:
    ep = ElectronEndpoint(
        label="gc2",
        app="grok",
        display="10",
        title="Empty Bot",
        agent_id="empty-agent-id",
    )
    empty = {
        "state": "chat_list",
        "active_title": "Empty Bot",
        "active_agent_id": "empty-agent-id",
        "titles": ["Empty Bot"],
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    pages = [{"title": "Grok Bot", "web_socket_debugger_url": "ws://fake"}]
    probe_calls = {"n": 0}

    async def _fake_probe(_ws: str) -> dict[str, Any]:
        probe_calls["n"] += 1
        return dict(empty)

    async def _fake_select_grok(_display: object, _agent_id: str) -> dict[str, Any]:
        return {"ok": True, "currentAgentId": "empty-agent-id"}

    good_verdict = {
        "ok": True,
        "active_agent_id": "empty-agent-id",
        "active_title": "Empty Bot",
    }

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            side_effect=_fake_select_grok,
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_grok_after_select",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "stale_dom": False,
                "surface": empty,
                "verdict": good_verdict,
            },
        ),
        patch("cursor_bridge.electron_chat_relay.agent_paste_lock", _noop_lock),
    ):
        snap = await read_endpoint_turn(ep, select=True)

    assert snap["ok"] is True
    assert snap["active_agent_id"] == "empty-agent-id"
    assert snap["recent_messages"] == []
    assert snap["latest_message"] == ""


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

    picked = surfaces[-1]
    pick_verdict = {
        "ok": True,
        "active_agent_id": "gc2-agent-uuid",
        "active_title": "Stage Manager (gc2)",
    }

    with (
        patch("cursor_bridge.electron_chat_relay.list_pages", return_value=pages),
        patch(
            "cursor_bridge.electron_chat_relay.probe_surface", side_effect=_fake_probe
        ),
        patch(
            "cursor_bridge.electron_chat_relay.select_grok_agent_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "currentAgentId": "gc2-agent-uuid"},
        ),
        patch(
            "cursor_bridge.electron_chat_relay.probe_grok_after_select",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "stale_dom": False,
                "surface": picked,
                "verdict": pick_verdict,
            },
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
