"""Offline tests for grok_computer CDP and update orchestration."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from cursor_bridge import grok_computer as gc

pytestmark = pytest.mark.offline


def _cdp_msg(mid: int, value: Any) -> str:
    return json.dumps({"id": mid, "result": {"result": {"value": value}}})


class _FakeWS:
    def __init__(self, inbox: list[str]) -> None:
        self._inbox = inbox

    async def send(self, _raw: str) -> None:
        return None

    async def recv(self) -> str:
        if not self._inbox:
            raise RuntimeError("no fake CDP messages")
        return self._inbox.pop(0)

    async def __aenter__(self) -> _FakeWS:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_reset_x11_screensaver_closes_display(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    fake.XOpenDisplay.return_value = 42

    monkeypatch.setattr(gc.ctypes.util, "find_library", lambda _name: "libX11.so.6")
    monkeypatch.setattr(gc.ctypes, "cdll", MagicMock())
    gc.ctypes.cdll.LoadLibrary.return_value = fake
    assert gc.reset_x11_screensaver(":9") is True
    fake.XResetScreenSaver.assert_called_once()
    fake.XCloseDisplay.assert_called_once()


@pytest.mark.asyncio
async def test_probe_grok_computer(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "phase": "settled",
        "egress": {"state": "connected"},
        "migration": {"phase": "idle"},
        "running_assistants": [],
    }
    inbox = [_cdp_msg(1, payload)]

    def _connect(_url: str, **_kw: Any) -> _FakeWS:
        return _FakeWS(inbox)

    monkeypatch.setattr(
        gc,
        "list_pages",
        lambda _origin, **_kw: [
            {
                "id": "1",
                "title": "Grok Bot",
                "url": "",
                "web_socket_debugger_url": "ws://fake",
            }
        ],
    )
    monkeypatch.setattr("websockets.connect", _connect)
    out = await gc.probe_grok_computer()
    assert out["phase"] == "settled"
    assert out["running_assistants"] == []


@pytest.mark.asyncio
async def test_wake_grok_computer_includes_x11_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gc, "reset_x11_screensaver", lambda _display: True)
    inbox = [_cdp_msg(1, {"phase": "following", "egress": {"state": "connected"}})]
    monkeypatch.setattr(
        gc,
        "list_pages",
        lambda _origin, **_kw: [
            {
                "id": "1",
                "title": "Grok Bot",
                "url": "",
                "web_socket_debugger_url": "ws://fake",
            }
        ],
    )
    monkeypatch.setattr("websockets.connect", lambda _url, **_kw: _FakeWS(inbox))
    out = await gc.wake_grok_computer()
    assert out["x11_reset"] is True
    assert out["egress"]["state"] == "connected"


@pytest.mark.asyncio
async def test_run_grok_computer_update_busy_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _probe(**_kw: Any) -> dict[str, Any]:
        return {"running_assistants": [{"id": "a1", "name": "busy"}]}

    monkeypatch.setattr(gc, "probe_grok_computer", _probe)
    out = await gc.run_grok_computer_update(require_idle=True)
    assert out["status"] == "busy_running_assistants"
    assert len(out["running_assistants"]) == 1


@pytest.mark.asyncio
async def test_run_grok_computer_update_store_not_durable_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _probe(**_kw: Any) -> dict[str, Any]:
        return {"running_assistants": []}

    async def _eval(ws_url: str, expression: str, **_kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": True,
                "migration": {"phase": "failed", "detail": "store-not-durable"},
            }
        if calls["n"] == 2:
            return {"ok": True, "migration": {"phase": "backing-up"}}
        if "getBoxMigrationStatus" in expression:
            return {"phase": "done", "operationId": "op-1"}
        if "setComputerViewOpen" in expression or "Close" in expression:
            return {"closed": True, "close_clicks": 1, "settings_still_open": False}
        del ws_url
        return {"phase": "settled", "egress": {"state": "connected"}, "migration": {}}

    monkeypatch.setattr(gc, "probe_grok_computer", _probe)
    monkeypatch.setattr(gc, "_resolve_ws_url", lambda *_a, **_k: "ws://fake")
    monkeypatch.setattr(gc, "_cdp_eval", _eval)

    async def _settled(*_a: Any, **_k: Any) -> dict[str, str]:
        return {"phase": "following"}

    monkeypatch.setattr(gc, "_wait_phase_settled", _settled)

    async def _wake(**_kw: Any) -> dict[str, Any]:
        return {"x11_reset": True, "phase": "settled"}

    monkeypatch.setattr(gc, "wake_grok_computer", _wake)

    out = await gc.run_grok_computer_update(poll_timeout_s=1.0)
    assert calls["n"] >= 2
    assert out["status"] == "done"
    assert out["keep_alive"]["x11_reset"] is True


@pytest.mark.asyncio
async def test_run_grok_computer_update_post_close_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wake_calls: list[str] = []

    async def _probe(**_kw: Any) -> dict[str, Any]:
        return {"running_assistants": []}

    async def _eval(_ws: str, expression: str, **_kw: Any) -> Any:
        if "Update Bot" in expression or "update_button" in expression:
            return {"ok": True, "migration": {"phase": "backing-up"}}
        if "getBoxMigrationStatus" in expression:
            return {"phase": "done"}
        if "setComputerViewOpen" in expression or "Close" in expression:
            return {"closed": True, "close_clicks": 1, "settings_still_open": False}
        return {}

    wake_kwargs: list[dict[str, Any]] = []

    async def _wake(**kw: Any) -> dict[str, Any]:
        wake_calls.append("wake")
        wake_kwargs.append(dict(kw))
        return {"x11_reset": True}

    monkeypatch.setattr(gc, "probe_grok_computer", _probe)
    monkeypatch.setattr(gc, "_resolve_ws_url", lambda *_a, **_k: "ws://fake")
    monkeypatch.setattr(gc, "_cdp_eval", _eval)
    monkeypatch.setattr(gc, "wake_grok_computer", _wake)

    out = await gc.run_grok_computer_update(poll_timeout_s=1.0)
    assert wake_calls == ["wake"]
    assert wake_kwargs[-1].get("open_computer_view") is False
    assert out["status"] == "up_to_date"
    assert out["migration"]["phase"] == "unchanged"


def test_update_scripts_use_settings_dialog_not_transcript_chip() -> None:
    assert "span[role=button]" not in gc._UPDATE_CLICK_JS
    assert "Grok Bot settings" in gc._UPDATE_CLICK_JS
    assert "Open account menu" in gc._UPDATE_CLICK_JS
    assert '[role=dialog][aria-label="Grok Bot settings"]' in gc._RETURN_TO_PORTAL_JS


def test_resolve_grok_cdp_port_uses_window_config() -> None:
    assert gc.resolve_grok_cdp_port("10", None) == 9340
    assert gc.resolve_grok_cdp_port(":10", 9339) == 9340
    assert gc.resolve_grok_cdp_port("9", 9339) == 9339
    assert gc.resolve_grok_cdp_port("10", 9999) == 9999


@pytest.mark.asyncio
async def test_stale_done_migration_is_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    same = {"phase": "done", "operationId": {"value": "same"}}

    async def _probe(**_kw: Any) -> dict[str, Any]:
        return {
            "running_assistants": [],
            "migration": same,
            "forever_box": {"imageUpdateAvailable": True},
        }

    async def _eval(_ws: str, expression: str, **_kw: Any) -> Any:
        if "Update Bot" in expression:
            return {"ok": True, "path": "account_settings"}
        if "getBoxMigrationStatus" in expression:
            return dict(same)
        if "setComputerViewOpen" in expression:
            return {"closed": True, "settings_still_open": False}
        return {}

    async def _wake(**_kw: Any) -> dict[str, Any]:
        return {"x11_reset": True}

    monkeypatch.setattr(gc, "probe_grok_computer", _probe)
    monkeypatch.setattr(gc, "_resolve_ws_url", lambda *_a, **_k: "ws://fake")
    monkeypatch.setattr(gc, "_cdp_eval", _eval)
    monkeypatch.setattr(gc, "wake_grok_computer", _wake)

    out = await gc.run_grok_computer_update(poll_timeout_s=0.4, max_update_passes=2)
    assert out["status"] == "failed"
    assert out["migration"]["phase"] == "unchanged"
    assert out["close"]["settings_still_open"] is False


@pytest.mark.asyncio
async def test_second_pass_clears_image_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = {"n": 0}
    polls = {"n": 0}

    async def _probe(**_kw: Any) -> dict[str, Any]:
        probes["n"] += 1
        return {
            "running_assistants": [],
            "migration": {"phase": "done", "operationId": {"value": "old"}},
            "forever_box": {"imageUpdateAvailable": probes["n"] < 3},
        }

    async def _eval(_ws: str, expression: str, **_kw: Any) -> Any:
        if "Update Bot" in expression:
            return {"ok": True, "path": "account_settings"}
        if "getBoxMigrationStatus" in expression:
            polls["n"] += 1
            return {"phase": "done", "operationId": {"value": f"new-{polls['n']}"}}
        if "setComputerViewOpen" in expression:
            return {"closed": True, "settings_still_open": False}
        return {}

    async def _wake(**_kw: Any) -> dict[str, Any]:
        return {"x11_reset": True}

    monkeypatch.setattr(gc, "probe_grok_computer", _probe)
    monkeypatch.setattr(gc, "_resolve_ws_url", lambda *_a, **_k: "ws://fake")
    monkeypatch.setattr(gc, "_cdp_eval", _eval)
    monkeypatch.setattr(gc, "wake_grok_computer", _wake)

    out = await gc.run_grok_computer_update(poll_timeout_s=2.0)
    assert out["status"] == "done"
    assert len(out["passes"]) == 2
    assert out["probe_after"]["forever_box"]["imageUpdateAvailable"] is False
