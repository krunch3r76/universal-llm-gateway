"""S1 report-only supervision on cursor_bridge chat_surface (offline)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from browser_governance.contract import (
    SUPERVISION_BANNER_SCAN_JS,
    classify_ui_banner_text,
)
from cursor_bridge.chat_surface import _read_surface, classify_surface, wake_decision
from cursor_bridge.electron_chat_relay import probe_electron_supervision

pytestmark = pytest.mark.offline


class FakePlaywrightError(Exception):
    pass


class FakeWebSocketError(Exception):
    pass


def _surface_without_supervision(surface: dict) -> dict:
    return {k: v for k, v in surface.items() if k != "supervision"}


def test_classify_state_and_wake_decision_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    items = [
        {"text": "18040 lane-x", "selected": True, "agent_id": "agent-uuid-1"},
        {"text": "18039 other", "selected": False, "agent_id": "agent-uuid-2"},
    ]
    surface = classify_surface([], items)
    core = _surface_without_supervision(surface)
    assert core["state"] == "chat_list"
    assert core["active_title"] == "18040 lane-x"
    verdict = wake_decision(
        core, thread="18040", slug="lane-x", agent_id="agent-uuid-1"
    )
    assert verdict["ok"] is True
    assert verdict["reason"] == "match"


def test_failed_probe_envelope_unknown_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    env = classify_surface([], [])["supervision"]
    assert env["scanned"] is False
    assert env["ui_error"]["present"] is None
    assert env["auth_state"] == "unknown"
    assert env["app_health"] is None


def test_message_prose_dom_excluded_from_banner_scan_js() -> None:
    assert ".sand-message-prose" in SUPERVISION_BANNER_SCAN_JS
    assert "contenteditable" in SUPERVISION_BANNER_SCAN_JS


def test_non_message_ui_banner_classified() -> None:
    kind, _ = classify_ui_banner_text("Session expired — sign in again")
    assert kind == "session_expired"


def test_ui_alert_banner_classified_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    long_text = "Session expired — " + ("x" * 200)
    surface = classify_surface(
        [],
        [{"text": "row", "selected": True, "agent_id": "a"}],
        ui_banner={
            "scanned": True,
            "present": True,
            "kind": "session_expired",
            "summary": long_text,
        },
    )
    summary = surface["supervision"]["ui_error"]["summary"]
    assert summary is not None
    assert len(summary) <= 160
    assert surface["supervision"]["ui_error"]["kind"] == "session_expired"


def test_no_active_chat_transient_not_stale_seat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    surface = classify_surface(
        [],
        [{"text": "18040 lane-x", "selected": False, "agent_id": "agent-uuid-1"}],
        has_active_chat=False,
    )
    assert surface["supervision"]["visibility_state"] == "no_active_chat_transient"
    core = _surface_without_supervision(surface)
    verdict = wake_decision(
        core, thread="18040", slug="lane-x", agent_id="agent-uuid-1"
    )
    assert verdict["ok"] is False
    assert verdict["reason"] == "no_active_chat"
    assert core["active_agent_id"] == ""


def test_gate_off_supervision_unscanned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    env = classify_surface(
        [],
        [{"text": "t", "selected": True, "agent_id": "a"}],
        ui_banner={"scanned": True, "present": True, "kind": "renderer_crashed"},
    )["supervision"]
    assert env["scanned"] is False
    assert env["ui_error"]["present"] is None


@pytest.mark.asyncio
async def test_read_surface_banner_scan_exception_is_unknown_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    probe_value = {
        "labels": [],
        "items": [{"text": "t", "selected": True, "agent_id": "a"}],
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
        "message_turns": [],
        "has_active_chat": True,
    }
    calls = {"banner": 0}

    async def _eval(ws_url: str, expression: str, **kwargs: Any) -> Any:
        if expression == SUPERVISION_BANNER_SCAN_JS:
            calls["banner"] += 1
            raise FakePlaywrightError("destroyed execution context")
        return probe_value

    with patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval):
        surface = await _read_surface("ws://fake", timeout_s=1.0)
    assert calls["banner"] == 1
    sup = surface["supervision"]
    assert sup["ui_error"]["scanned"] is False
    assert sup["ui_error"]["present"] is None


@pytest.mark.asyncio
async def test_read_surface_gate_off_skips_banner_evaluate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    probe_value = {
        "labels": [],
        "items": [],
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
        "message_turns": [],
        "has_active_chat": None,
    }
    banner_calls = 0

    async def _eval(ws_url: str, expression: str, **kwargs: Any) -> Any:
        nonlocal banner_calls
        if "role='alert'" in expression or "selectors" in expression:
            banner_calls += 1
        return probe_value

    with patch("cursor_bridge.chat_surface._evaluate_cdp_js", side_effect=_eval):
        await _read_surface("ws://fake", timeout_s=1.0)
    assert banner_calls == 0


@pytest.mark.asyncio
async def test_probe_electron_supervision_ws_exception_unscanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")

    async def _probe(**_: Any) -> dict[str, Any]:
        return {"selected_agent_id": "a", "phase": "ok"}

    async def _eval(*_a: Any, **_k: Any) -> Any:
        raise FakeWebSocketError("closed")

    with patch(
        "cursor_bridge.electron_chat_relay.probe_grok_computer",
        side_effect=_probe,
    ), patch(
        "cursor_bridge.electron_chat_relay._grok_ws_url",
        return_value="ws://x",
    ), patch(
        "cursor_bridge.electron_chat_relay._evaluate_cdp_js",
        side_effect=_eval,
    ):
        env = await probe_electron_supervision(display=":9", cdp_port=9339)
    assert env["ui_error"]["scanned"] is False
    assert env["ui_error"]["present"] is None
