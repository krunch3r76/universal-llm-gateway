"""Tests for Grok Bot box MCP egress watch loop (friction:39438)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from deploy_identity.mcp_edge_events import (
    mcp_edge_grokbot_loader_stuck,
    mcp_edge_grokbot_probe_failed,
    mcp_edge_grokbot_tunnel_stale,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_io import (
    GC1_CLIENT_IP,
    HM1_CLIENT_IPS,
    build_snapshot_from_inputs,
    loader_stuck_from_rows,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GrokbotEgressLoopState,
    GrokbotEgressSnapshot,
    decide_grokbot_tick,
    watch_connection_state,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_loop import (
    GrokbotEgressWatchLoop,
)

pytestmark = pytest.mark.offline

_NOW_MS = 1_700_000_000_000


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _base_snapshot(**overrides: object) -> GrokbotEgressSnapshot:
    base = dict(
        desktop_running=True,
        desktop_pid=99,
        watch_connected=False,
        watch_disconnected_s=65.0,
        daemon_log_readable=True,
        health_http_status=200,
        tunnel_stale=True,
        loader_stuck=False,
        loader_stuck_s=None,
        hm1_tools_call_ok=True,
        undici_idle_s=None,
        in_flight_gc1_mcp=False,
        open_gc1_bus_turn=False,
    )
    base.update(overrides)
    return GrokbotEgressSnapshot(**base)  # type: ignore[arg-type]


def _event_row(
    *,
    ts_ms: int,
    signal: str,
    client_ip: str = GC1_CLIENT_IP,
    mcp_method: str = "",
    user_agent: str = "",
    jsonrpc_id: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {"client_ip": client_ip}
    if mcp_method:
        payload["mcp_method"] = mcp_method
    if user_agent:
        payload["user_agent"] = user_agent
    if jsonrpc_id:
        payload["jsonrpc_id"] = jsonrpc_id
    return {"ts_unix_ms": ts_ms, "signal": signal, "payload": payload}


class _CaptureEmit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def __call__(
        self,
        signal: str,
        payload: dict[str, object],
        **_: object,
    ) -> None:
        self.events.append((signal, payload))


def test_tunnel_stale_restarts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    restarts: list[float] = []

    async def snap() -> GrokbotEgressSnapshot:
        return _base_snapshot()

    async def restart() -> None:
        restarts.append(time.time())

    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"),
        snapshot_fn=snap,
        restart_desktop_fn=restart,
    )
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )

    _run(loop._tick())
    assert any(sig == "mcp.edge.grokbot_tunnel.stale" for sig, _ in capture.events)
    assert len(restarts) == 1

    _run(loop._tick())
    assert len(restarts) == 1


def test_loader_stuck_event() -> None:
    t0 = _NOW_MS - 240_000
    rows = [
        _event_row(ts_ms=t0, signal="mcp.transport.request.completed", mcp_method="initialize"),
        _event_row(
            ts_ms=t0 + 1000,
            signal="mcp.transport.request.completed",
            mcp_method="tools/list",
        ),
        _event_row(
            ts_ms=t0 + 5000,
            signal="mcp.transport.request.completed",
            client_ip=HM1_CLIENT_IPS[0],
            mcp_method="tools/call",
        ),
    ]
    stuck, since_s = loader_stuck_from_rows(rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000)
    assert stuck is True
    assert since_s is not None and since_s >= 180.0

    snap = build_snapshot_from_inputs(
        now_ms=_NOW_MS,
        desktop_running=True,
        desktop_pid=1,
        daemon_log="[user-computer-watch] 2026-01-01T00:00:00.000Z connected after 1 ms out (attempts 1)\n",
        health_http_status=200,
        event_rows=rows,
        open_bus_turn=False,
    )
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=_NOW_MS / 1000, state=state, snapshot=snap)
    assert decision.emit_loader_stuck is True
    assert decision.restart_desktop is False


def test_loader_no_desktop_restart_before_backstop() -> None:
    snap = _base_snapshot(
        tunnel_stale=False,
        watch_connected=True,
        watch_disconnected_s=None,
        loader_stuck=True,
        loader_stuck_s=200.0,
    )
    state = GrokbotEgressLoopState()
    state.loader_stuck_first_at = time.time() - 300.0
    state.loader_stuck_emitted_at = state.loader_stuck_first_at
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.restart_desktop is False


def test_thirty_min_cap() -> None:
    snap = _base_snapshot()
    state = GrokbotEgressLoopState()
    state.last_desktop_restart_at = time.time() - 100.0
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.restart_desktop is False


def test_completed_request_does_not_defer() -> None:
    snap = _base_snapshot(
        in_flight_gc1_mcp=False,
        open_gc1_bus_turn=False,
    )
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.restart_desktop is True


def test_in_flight_defers() -> None:
    snap = _base_snapshot(in_flight_gc1_mcp=True)
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.restart_desktop is False
    assert state.deferral_started_at is not None


def test_deferral_ceiling() -> None:
    snap = _base_snapshot(in_flight_gc1_mcp=True)
    state = GrokbotEgressLoopState()
    now = time.time()
    state.deferral_started_at = now - 601.0
    decision = decide_grokbot_tick(now=now, state=state, snapshot=snap)
    assert decision.restart_desktop is True


def test_health_non_200_no_events() -> None:
    snap = _base_snapshot(health_http_status=503, tunnel_stale=True)
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.emit_tunnel_stale is False
    assert decision.emit_loader_stuck is False
    assert decision.restart_desktop is False


def test_unreadable_log() -> None:
    snap = _base_snapshot(daemon_log_readable=False)
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.emit_probe_failed is True
    assert decision.restart_desktop is False
    failed = mcp_edge_grokbot_probe_failed(
        error_class=decision.probe_error_class,
        detail=decision.probe_detail,
    )
    assert failed.signal == "mcp.edge.grokbot.probe.failed"


def test_connected_gap_warn_only() -> None:
    snap = _base_snapshot(
        tunnel_stale=False,
        watch_connected=True,
        watch_disconnected_s=None,
        undici_idle_s=1300.0,
    )
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
    assert decision.warn_undici_gap is True
    assert decision.restart_desktop is False
    assert decision.emit_tunnel_stale is False


def test_watch_connection_state_dialing() -> None:
    log = (
        "[user-computer-watch] 2026-01-01T00:00:00.000Z dialing (attempt 1)\n"
    )
    connected, gap = watch_connection_state(log, now_ms=_NOW_MS)
    assert connected is False
    assert gap is not None and gap >= 0


def test_event_factories() -> None:
    assert (
        mcp_edge_grokbot_tunnel_stale(disconnected_s=61.0, desktop_pid=1).signal
        == "mcp.edge.grokbot_tunnel.stale"
    )
    assert (
        mcp_edge_grokbot_loader_stuck(
            since_tools_list_s=200.0, gc1_client_ip=GC1_CLIENT_IP
        ).signal
        == "mcp.edge.grokbot_loader.stuck"
    )
