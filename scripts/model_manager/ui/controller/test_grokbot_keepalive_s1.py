"""S1 :10 keep-alive parity for Grokbot egress watch (offline)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.ui.controller.grokbot_egress_watch_io import (
    resolve_grok_keepalive_targets,
    secondary_display_running,
)
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
        desktop_running=False,
        desktop_pid=None,
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


def test_resolve_keepalive_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ULG_GROK_KEEPALIVE_DISPLAYS", raising=False)
    targets = resolve_grok_keepalive_targets()
    displays = {d.lstrip(":") for d, _ in targets}
    assert "9" in displays
    assert "10" in displays


@pytest.mark.parametrize(
    ("env_val", "expect"),
    [
        ("off", set()),
        ("9", {"9"}),
        ("10", {"10"}),
        ("9,10", {"9", "10"}),
    ],
)
def test_keepalive_displays_env(
    monkeypatch: pytest.MonkeyPatch, env_val: str, expect: set[str]
) -> None:
    monkeypatch.setenv("ULG_GROK_KEEPALIVE_DISPLAYS", env_val)
    targets = resolve_grok_keepalive_targets()
    assert {d.lstrip(":") for d, _ in targets} == expect


def test_secondary_keepalive_when_primary_desktop_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    wakes: list[tuple[str, int]] = []

    def _wake(*, display: str = ":9", cdp_port: int = 9339, **_k: object) -> dict:
        wakes.append((display, cdp_port))
        return {"ok": True}

    def _secondary(_display: str, cdp_port: int) -> bool:
        return cdp_port == 9340

    monkeypatch.setattr(loop_mod, "keep_alive_grokbot_presence", _wake)
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"),
        keepalive_targets_fn=lambda: [(":9", 9339), (":10", 9340)],
        secondary_keepalive_fn=_secondary,
    )
    _run(loop._maybe_keepalive_displays(_snap(desktop_running=False)))
    assert (":10", 9340) in wakes
    assert (":9", 9339) not in wakes


def test_unit_test_snapshot_fn_no_socket_dials_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    def _boom(*_a: object, **_k: object) -> bool:
        raise AssertionError("secondary_display_running must not run")

    monkeypatch.setattr(loop_mod, "secondary_display_running", _boom)
    monkeypatch.delenv("ULG_GROK_KEEPALIVE_DISPLAYS", raising=False)
    wakes: list[int] = []

    def _wake(**_k: object) -> dict:
        wakes.append(1)
        return {"ok": True}

    async def _snap_fn() -> GrokbotEgressSnapshot:
        return _snap(desktop_running=True)

    monkeypatch.setattr(loop_mod, "keep_alive_grokbot_presence", _wake)
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"),
        snapshot_fn=_snap_fn,
    )
    _run(loop._maybe_keepalive_displays(_snap(desktop_running=True)))
    assert len(wakes) == 1


def test_secondary_display_running_uses_injected_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = tmp_path / "desktop-status.json"
    status.write_text('{"pid": 99999}\n', encoding="utf-8")
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_io.read_desktop_status",
        lambda _path: {"pid": 99999},
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_io.desktop_pid_alive",
        lambda _s: (True, 99999),
    )
    assert secondary_display_running(":10", 9340) is True
