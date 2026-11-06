"""F2 keep-alive, app-health poll, and status-path repairs (offline)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.ui.controller import grokbot_egress_watch_io as io_mod
from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GrokbotEgressSnapshot,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_loop import (
    GrokbotEgressWatchLoop,
)

pytestmark = pytest.mark.offline


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _snap(**overrides: object) -> GrokbotEgressSnapshot:
    base = dict(
        desktop_running=True,
        desktop_pid=1,
        watch_connected=True,
        watch_disconnected_s=None,
        daemon_log_readable=True,
        health_http_status=200,
        tunnel_stale=False,
        loader_stuck=False,
        loader_stuck_s=None,
        hm1_tools_call_ok=True,
        undici_idle_s=None,
        in_flight_gc1_mcp=False,
        open_gc1_bus_turn=False,
    )
    base.update(overrides)
    return GrokbotEgressSnapshot(**base)  # type: ignore[arg-type]


def test_desktop_status_path_unmapped_display() -> None:
    assert io_mod.desktop_status_path_for_display(":9") == io_mod.DEFAULT_DESKTOP_STATUS
    assert io_mod.desktop_status_path_for_display(":10") == io_mod.DEFAULT_GC2_DESKTOP_STATUS
    assert io_mod.desktop_status_path_for_display(":8") is None
    assert io_mod.desktop_status_path_for_display(":11") is None


def test_malformed_keepalive_config_falls_back_to_nine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> dict[str, object]:
        raise ValueError("corrupt json")

    monkeypatch.setattr(
        "cursor_bridge.grok_window_bindings.load_config",
        _boom,
    )
    targets = io_mod.resolve_grok_keepalive_targets()
    assert targets == [(":9", 9339)]


def test_keepalive_display_nine_survives_ten_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    calls: list[str] = []

    def _wake(**kwargs: object) -> dict[str, object]:
        disp = str(kwargs.get("display") or "")
        calls.append(disp)
        if disp == ":10":
            raise RuntimeError("cdp down")
        return {"ok": True}

    monkeypatch.setattr(loop_mod, "keep_alive_grokbot_presence", _wake)
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"),
        keepalive_targets_fn=lambda: [(":9", 9339), (":10", 9340)],
        secondary_keepalive_fn=lambda _d, _p: True,
    )
    _run(loop._maybe_keepalive_displays(_snap(desktop_running=True)))
    assert ":9" in calls
    assert ":10" in calls


def test_app_health_poll_emits_report_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    emitted: list[tuple[str, dict[str, object]]] = []

    async def _emit(signal: str, payload: dict[str, object], **_k: object) -> None:
        emitted.append((signal, payload))

    async def _probe(**_kw: object) -> dict[str, object]:
        return {
            "phase": "settled",
            "egress": {"state": "error"},
            "forever_box": {"status": "ok"},
        }

    monkeypatch.setattr(loop_mod, "_emit", _emit)
    monkeypatch.setattr(
        "cursor_bridge.grok_computer.probe_grok_computer",
        _probe,
    )
    monkeypatch.setenv("ULG_SUPERVISION_POLL_MODE", "observe")
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"),
        keepalive_targets_fn=lambda: [(":9", 9339)],
    )
    loop._last_app_health_poll_at = None
    monkeypatch.delenv("ULG_GROK_KEEPALIVE_DISPLAYS", raising=False)
    _run(loop._maybe_poll_grok_app_health(now=1000.0, snapshot=_snap()))
    assert emitted
    signal, payload = emitted[0]
    assert signal == "browser.supervision.error"
    assert payload.get("visibility_state") == "report_only"
    assert "egress_error" in str(payload.get("summary") or "")


def test_app_health_poll_off_skips(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setenv("ULG_SUPERVISION_POLL_MODE", "off")

    def _boom(**_k: object) -> dict[str, object]:
        raise AssertionError("probe must not run when poll mode is off")

    monkeypatch.setattr(
        "cursor_bridge.grok_computer.probe_grok_computer",
        _boom,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    _run(loop._maybe_poll_grok_app_health(now=1000.0, snapshot=_snap()))


def test_grok_app_health_issue_kinds_unknown() -> None:
    assert io_mod.grok_app_health_issue_kinds(None) == ["probe_unknown"]
    errors, observes = io_mod.grok_app_health_classify({"phase": "asleep"})
    assert not errors
    assert "phase_asleep" in observes


def test_malformed_keepalive_respects_off_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_GROK_KEEPALIVE_DISPLAYS", "off")

    def _boom() -> dict[str, object]:
        raise ValueError("corrupt json")

    monkeypatch.setattr(
        "cursor_bridge.grok_window_bindings.load_config",
        _boom,
    )
    assert io_mod.resolve_grok_keepalive_targets() == []


def test_substrate_status_import_error_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[3] / "web-automation-substrate-status.py"
    spec = importlib.util.spec_from_file_location("substrate_status_f2", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "browser_governance.contract", None)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_load_lanes", lambda: {})
    doc = mod.build_report()
    assert doc["resource_census"].get("error") == "ImportError"
