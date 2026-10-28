"""Tests for Grok Bot box MCP egress watch loop (friction:39438)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from deploy_identity.mcp_edge_events import (
    mcp_edge_grokbot_loader_stuck,
    mcp_edge_grokbot_probe_failed,
    mcp_edge_grokbot_tunnel_stale,
    mcp_edge_grokbot_watch_ok,
)

from scripts.model_manager.ui.controller.grokbot_egress_watch_io import (
    GC1_BUS_TIP_STALE_MS,
    GC1_CLIENT_IP,
    HM1_CLIENT_IPS,
    IN_FLIGHT_STALE_MS,
    build_snapshot_from_inputs,
    gc1_bus_turn_in_flight,
    in_flight_gc1_mcp_from_rows,
    loader_stuck_from_rows,
    query_events_rows,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    _HM1_FALSE_RECOVERY_CONSECUTIVE_N,
    _LOADER_DESKTOP_BACKSTOP_S,
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


def _watch_log_dialing(*, disconnected_ms: int = 90_000) -> str:
    from datetime import datetime

    dial_at = (
        datetime.fromtimestamp((_NOW_MS - disconnected_ms) / 1000.0, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z"
    )
    return f"[user-computer-watch] {dial_at} dialing (attempt 1)\n"


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
    method: str = "",
    path: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {"client_ip": client_ip}
    if mcp_method:
        payload["mcp_method"] = mcp_method
    if user_agent:
        payload["user_agent"] = user_agent
    if jsonrpc_id:
        payload["jsonrpc_id"] = jsonrpc_id
    if method:
        payload["method"] = method
    if path:
        payload["path"] = path
    return {"ts_unix_ms": ts_ms, "signal": signal, "payload": payload}


def _undici_edge_post(*, ts_ms: int, path: str = "/mcp/code/") -> dict[str, object]:
    return _event_row(
        ts_ms=ts_ms,
        signal="mcp.edge.request.observed",
        method="POST",
        path=path,
        user_agent="undici/6.0.0",
    )


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
        _event_row(
            ts_ms=t0, signal="mcp.transport.request.completed", mcp_method="initialize"
        ),
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
    stuck, since_s = loader_stuck_from_rows(
        rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000
    )
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
    t0 = _NOW_MS - 5_000
    rows = [
        _event_row(
            ts_ms=t0,
            signal="mcp.transport.request.started",
            mcp_method="tools/list",
            jsonrpc_id="7",
        ),
        _event_row(
            ts_ms=t0 + 500,
            signal="mcp.transport.request.completed",
            mcp_method="tools/list",
            jsonrpc_id="7",
        ),
    ]
    assert (
        in_flight_gc1_mcp_from_rows(rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000)
        is False
    )
    snap = build_snapshot_from_inputs(
        now_ms=_NOW_MS,
        desktop_running=True,
        desktop_pid=1,
        daemon_log=_watch_log_dialing(),
        health_http_status=200,
        event_rows=rows,
        open_bus_turn=False,
    )
    assert snap.tunnel_stale is True
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(now=_NOW_MS / 1000, state=state, snapshot=snap)
    assert decision.restart_desktop is True


def test_get_without_jsonrpc_id_does_not_defer() -> None:
    t0 = _NOW_MS - 2_000
    rows = [
        _event_row(
            ts_ms=t0,
            signal="mcp.transport.request.started",
            method="GET",
            mcp_method="",
        ),
        _event_row(
            ts_ms=t0 + 100,
            signal="mcp.transport.request.completed",
            method="GET",
            mcp_method="",
        ),
    ]
    assert (
        in_flight_gc1_mcp_from_rows(rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000)
        is False
    )
    snap = build_snapshot_from_inputs(
        now_ms=_NOW_MS,
        desktop_running=True,
        desktop_pid=1,
        daemon_log=_watch_log_dialing(),
        health_http_status=200,
        event_rows=rows,
        open_bus_turn=False,
    )
    assert snap.tunnel_stale is True
    decision = decide_grokbot_tick(
        now=_NOW_MS / 1000,
        state=GrokbotEgressLoopState(),
        snapshot=snap,
    )
    assert decision.restart_desktop is True


def test_open_tools_call_defers_from_rows() -> None:
    t0 = _NOW_MS - 1_000
    rows = [
        _undici_edge_post(ts_ms=t0),
        _event_row(
            ts_ms=t0,
            signal="mcp.transport.request.started",
            mcp_method="tools/call",
            jsonrpc_id="3",
        ),
    ]
    assert (
        in_flight_gc1_mcp_from_rows(rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000)
        is True
    )


def test_non_gc1_caller_on_shared_ip_does_not_defer() -> None:
    """Finding 1: io-host IP without undici edge correlation is not gc1 in-flight."""
    t0 = _NOW_MS - 1_000
    rows = [
        _event_row(
            ts_ms=t0,
            signal="mcp.transport.request.started",
            mcp_method="tools/call",
            jsonrpc_id="8",
        ),
    ]
    assert (
        in_flight_gc1_mcp_from_rows(rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000)
        is False
    )


def test_gc1_space_subject_verbs_are_terminal() -> None:
    """Finding 2: live tips use space-separated verbs, not pipe-only prefixes."""
    assert (
        gc1_bus_turn_in_flight(from_agent="grok-bot-gc1", subject="STATUS 39434 x")
        is False
    )
    assert (
        gc1_bus_turn_in_flight(from_agent="grok-bot-gc1", subject="PICKUP #1053 x")
        is False
    )
    assert (
        gc1_bus_turn_in_flight(from_agent="grok-bot-gc1", subject="LANDED commit ok")
        is False
    )


def test_stale_dispatch_admit_tip_does_not_defer() -> None:
    """Finding 3: old cursor-sdk generate admitted tip is not in-flight."""
    tip_ms = _NOW_MS - GC1_BUS_TIP_STALE_MS - 5_000
    assert (
        gc1_bus_turn_in_flight(
            from_agent="grok-bot-gc1",
            subject="cursor-sdk generate admitted",
            tip_created_ms=tip_ms,
            now_ms=_NOW_MS,
        )
        is False
    )
    assert (
        gc1_bus_turn_in_flight(
            from_agent="grok-bot-gc1",
            subject="cursor-sdk generate admitted",
            tip_created_ms=_NOW_MS - 1_000,
            now_ms=_NOW_MS,
        )
        is True
    )


def test_jsonrpc_id_collision_keeps_later_open_call() -> None:
    t0 = _NOW_MS - 3_000
    rows = [
        _undici_edge_post(ts_ms=t0),
        _event_row(
            ts_ms=t0,
            signal="mcp.transport.request.started",
            mcp_method="tools/call",
            jsonrpc_id="1",
        ),
        _undici_edge_post(ts_ms=t0 + 100),
        _event_row(
            ts_ms=t0 + 100,
            signal="mcp.transport.request.started",
            mcp_method="tools/call",
            jsonrpc_id="1",
        ),
        _event_row(
            ts_ms=t0 + 200,
            signal="mcp.transport.request.completed",
            mcp_method="tools/call",
            jsonrpc_id="1",
        ),
    ]
    assert (
        in_flight_gc1_mcp_from_rows(rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - 600_000)
        is True
    )


def test_stale_open_call_not_in_flight_past_ceiling() -> None:
    t0 = _NOW_MS - IN_FLIGHT_STALE_MS - 1_000
    rows = [
        _event_row(
            ts_ms=t0,
            signal="mcp.transport.request.started",
            mcp_method="tools/call",
            jsonrpc_id="99",
        ),
    ]
    assert (
        in_flight_gc1_mcp_from_rows(
            rows, now_ms=_NOW_MS, floor_ms=_NOW_MS - IN_FLIGHT_STALE_MS - 5_000
        )
        is False
    )


def test_historical_gc1_bus_turn_does_not_defer() -> None:
    assert (
        gc1_bus_turn_in_flight(from_agent="grok-bot-gc1", subject="VERDICT: CHANGES")
        is False
    )
    assert gc1_bus_turn_in_flight(from_agent="cursor", subject="REQUEST|x") is False
    assert (
        gc1_bus_turn_in_flight(from_agent="grok-bot-gc1", subject="REQUEST|stage")
        is True
    )


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
    log = "[user-computer-watch] 2026-01-01T00:00:00.000Z dialing (attempt 1)\n"
    connected, gap = watch_connection_state(log, now_ms=_NOW_MS)
    assert connected is False
    assert gap is not None and gap >= 0


def test_query_events_rows_without_uvicorn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """query_client import must not load event_store.server (uvicorn)."""
    import sys
    from contextlib import contextmanager
    from types import ModuleType

    sock = tmp_path / "events-query.sock"
    sock.write_text("", encoding="utf-8")
    monkeypatch.setenv("EVENTS_QUERY_SOCK", str(sock))
    for name in list(sys.modules):
        if name == "event_store" or name.startswith("event_store."):
            del sys.modules[name]

    uvicorn_block = ModuleType("uvicorn")

    def _uvicorn_import(name: str, *args: object, **kwargs: object) -> ModuleType:
        raise ImportError("No module named 'uvicorn'")

    uvicorn_block.__getattr__ = lambda _name: (_ for _ in ()).throw(  # type: ignore[method-assign]
        ImportError("No module named 'uvicorn'")
    )
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_block)

    class _FakeResponse:
        def json(self) -> dict[str, object]:
            return {"rows": [{"n": 1}]}

        status_code = 200

    class _FakeClient:
        def post(self, *_args: object, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse()

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    @contextmanager
    def _fake_client(*_args: object, **_kwargs: object):
        yield _FakeClient()

    monkeypatch.setattr("event_store.query_client.make_sync_client", _fake_client)

    rows = query_events_rows(tmp_path, "SELECT 1", "x")
    assert rows == [{"n": 1}]
    assert "event_store.server" not in sys.modules


def test_query_events_rows_default_socket_without_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from transport_utils.client_factory import DEFAULT_EVENTS_QUERY_SOCK_PATH

    monkeypatch.delenv("EVENTS_QUERY_SOCK", raising=False)
    seen: dict[str, str] = {}

    def _query_sql(
        sql: str, params: list[object] | None = None, limit: int = 100, **kwargs: object
    ) -> dict[str, object]:
        seen["url"] = str(kwargs.get("url"))
        return {"rows": []}

    monkeypatch.setattr("event_store.query_client.query_sql", _query_sql)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_io.os.path.exists",
        lambda path: path == DEFAULT_EVENTS_QUERY_SOCK_PATH,
    )

    query_events_rows(tmp_path, "SELECT 1")
    assert seen["url"] == f"unix://{DEFAULT_EVENTS_QUERY_SOCK_PATH}"


def test_query_events_rows_missing_socket_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVENTS_QUERY_SOCK", str(tmp_path / "absent.sock"))

    with pytest.raises(RuntimeError, match="events query socket missing"):
        query_events_rows(tmp_path, "SELECT 1")


def test_snapshot_query_failure_warns_once_without_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Blind query failure: one query warning; stale watch still fail-opens tunnel emit."""
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    dialing_log = _watch_log_dialing(disconnected_ms=90_000)
    monkeypatch.setattr(loop_mod.time, "time", lambda: _NOW_MS / 1000.0)
    monkeypatch.setattr(loop_mod, "read_desktop_status", lambda _path: {"pid": 1})
    monkeypatch.setattr(loop_mod, "desktop_pid_alive", lambda _status: (True, 1))
    monkeypatch.setattr(loop_mod, "read_daemon_log", lambda _path: dialing_log)
    monkeypatch.setattr(loop_mod, "external_probe_hostname", lambda: "example.invalid")
    monkeypatch.setattr(loop_mod, "resolve_a_records", lambda _host: [])
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_io.open_gc1_bus_turn",
        lambda **_kwargs: False,
    )

    def _boom(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("events query socket missing: /tmp/absent.sock")

    monkeypatch.setattr(loop_mod, "query_events_rows", _boom)

    emits: list[str] = []

    async def _no_emit(*args: object, **_kwargs: object) -> None:
        emits.append(str(args[0]) if args else "")

    monkeypatch.setattr(loop_mod, "_emit", _no_emit)
    restarted: list[int] = []

    async def _no_restart() -> None:
        restarted.append(1)

    query_warnings: list[str] = []
    monkeypatch.setattr(
        loop_mod.logger,
        "warning",
        lambda msg, *args: query_warnings.append(msg % args if args else str(msg)),
    )

    watch = GrokbotEgressWatchLoop(
        workspace_root=tmp_path, restart_desktop_fn=_no_restart
    )
    _run(watch._tick())
    assert len(query_warnings) == 1
    assert "grokbot egress event query failed" in query_warnings[0]
    assert any("grokbot_tunnel.stale" in e for e in emits)
    assert len(restarted) == 1


_WATCH_OK_SIGNAL = "mcp.edge.grokbot.watch.ok"
_FAILURE_SIGNALS = frozenset(
    {
        "mcp.edge.grokbot.probe.failed",
        "mcp.edge.grokbot_tunnel.stale",
        "mcp.edge.grokbot_loader.stuck",
    }
)


def _healthy_snapshot(**overrides: object) -> GrokbotEgressSnapshot:
    base = dict(
        tunnel_stale=False,
        watch_connected=True,
        watch_disconnected_s=None,
        loader_stuck=False,
        loader_stuck_s=None,
        health_http_status=200,
        hm1_tools_call_ok=True,
        undici_idle_s=45.0,
    )
    base.update(overrides)
    return _base_snapshot(**base)


async def _noop_restart() -> None:
    return None


async def _tick_with_snapshot(
    loop: GrokbotEgressWatchLoop,
    snap: GrokbotEgressSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now_s: float | None = None,
) -> None:
    async def snap_fn() -> GrokbotEgressSnapshot:
        return snap

    loop._snapshot_fn = snap_fn  # type: ignore[method-assign]
    if now_s is not None:
        import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

        monkeypatch.setattr(loop_mod.time, "time", lambda: now_s)
    await loop._tick()


def test_healthy_tick_emits_watch_ok_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _healthy_snapshot()
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=1_000_000.0))
    ok = [sig for sig, _ in capture.events if sig == _WATCH_OK_SIGNAL]
    assert len(ok) == 1
    assert not any(sig in _FAILURE_SIGNALS for sig, _ in capture.events)
    _, payload = next(p for p in capture.events if p[0] == _WATCH_OK_SIGNAL)
    assert payload["watch_connected"] is True
    assert payload["health_http_status"] == 200
    assert payload["hm1_tools_call_ok"] is True
    assert payload["undici_idle_s"] == 45.0


def test_healthy_tick_rate_limited_within_120s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _healthy_snapshot()
    t0 = 2_000_000.0
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0))
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0 + 30.0))
    ok = [sig for sig, _ in capture.events if sig == _WATCH_OK_SIGNAL]
    assert len(ok) == 1


def test_failing_tick_emits_no_watch_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _base_snapshot(daemon_log_readable=False)
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=3_000_000.0))
    assert not any(sig == _WATCH_OK_SIGNAL for sig, _ in capture.events)


def test_watch_ok_after_failure_resets_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    healthy = _healthy_snapshot()
    failing = _base_snapshot(daemon_log_readable=False)
    t0 = 4_000_000.0
    _run(_tick_with_snapshot(loop, healthy, monkeypatch, now_s=t0))
    _run(_tick_with_snapshot(loop, failing, monkeypatch, now_s=t0 + 15.0))
    _run(_tick_with_snapshot(loop, healthy, monkeypatch, now_s=t0 + 45.0))
    ok = [sig for sig, _ in capture.events if sig == _WATCH_OK_SIGNAL]
    assert len(ok) == 2


def _ok_payloads(
    events: list[tuple[str, dict[str, object]]],
) -> list[dict[str, object]]:
    return [payload for sig, payload in events if sig == _WATCH_OK_SIGNAL]


def test_consecutive_tunnel_stale_ticks_emit_no_watch_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"), restart_desktop_fn=_noop_restart
    )
    snap = _base_snapshot()
    t0 = 5_000_000.0
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0))
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0 + 30.0))
    assert _ok_payloads(capture.events) == []


def test_consecutive_loader_stuck_ticks_emit_no_watch_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _healthy_snapshot(loader_stuck=True, loader_stuck_s=200.0)
    t0 = 6_000_000.0
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0))
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0 + 30.0))
    assert _ok_payloads(capture.events) == []


def test_desktop_down_emits_no_watch_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _healthy_snapshot(
        desktop_running=False,
        desktop_pid=None,
        watch_connected=False,
        tunnel_stale=False,
    )
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=7_000_000.0))
    assert _ok_payloads(capture.events) == []


def test_health_500_emits_no_watch_ok_and_clears_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    healthy = _healthy_snapshot()
    bad = _healthy_snapshot(health_http_status=500)
    t0 = 8_000_000.0
    _run(_tick_with_snapshot(loop, healthy, monkeypatch, now_s=t0))
    _run(_tick_with_snapshot(loop, bad, monkeypatch, now_s=t0 + 30.0))
    _run(_tick_with_snapshot(loop, healthy, monkeypatch, now_s=t0 + 60.0))
    payloads = _ok_payloads(capture.events)
    assert len(payloads) == 2
    assert all(p["health_http_status"] == 200 for p in payloads)


def test_unchecked_health_emits_watch_ok_with_health_checked_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _healthy_snapshot(health_http_status=None)
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=9_000_000.0))
    payloads = _ok_payloads(capture.events)
    assert len(payloads) == 1
    assert payloads[0]["health_checked"] is False
    assert payloads[0]["health_http_status"] is None


def test_checked_health_200_sets_health_checked_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    _run(
        _tick_with_snapshot(loop, _healthy_snapshot(), monkeypatch, now_s=10_000_000.0)
    )
    payloads = _ok_payloads(capture.events)
    assert len(payloads) == 1
    assert payloads[0]["health_checked"] is True
    assert payloads[0]["health_http_status"] == 200


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
    ok = mcp_edge_grokbot_watch_ok(
        watch_connected=True,
        health_http_status=200,
        hm1_tools_call_ok=True,
        undici_idle_s=12.34,
        health_checked=True,
    )
    assert ok.signal == _WATCH_OK_SIGNAL
    assert ok.payload["undici_idle_s"] == 12.3
    assert ok.payload["health_checked"] is True
    assert set(ok.payload) == {
        "watch_connected",
        "health_http_status",
        "hm1_tools_call_ok",
        "undici_idle_s",
        "health_checked",
    }


_CONNECTED_LOG = (
    "[user-computer-watch] 2026-01-01T00:00:00.000Z connected after 1 ms "
    "out (attempts 1)\n"
)


def _patch_default_snapshot_io(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetch: Any,
    addresses: list[str] | None = None,
    host: str = "mcp.example",
) -> None:
    """Replace live desktop, DNS, TLS, events, and bus reads for one test."""
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    monkeypatch.setattr(loop_mod, "read_desktop_status", lambda _path: {"pid": 99})
    monkeypatch.setattr(loop_mod, "desktop_pid_alive", lambda _status: (True, 99))
    monkeypatch.setattr(loop_mod, "read_daemon_log", lambda _path: _CONNECTED_LOG)
    monkeypatch.setattr(loop_mod, "external_probe_hostname", lambda: host)
    monkeypatch.setattr(
        loop_mod,
        "resolve_a_records",
        lambda _host: [] if addresses is None else addresses,
    )
    monkeypatch.setattr(loop_mod, "fetch_health", fetch)
    monkeypatch.setattr(loop_mod, "query_events_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_io.open_gc1_bus_turn",
        lambda **_kwargs: False,
    )


def test_probe_skipped_no_public_ipv4_emits_ok_unchecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    capture = _CaptureEmit()
    monkeypatch.setattr(loop_mod, "_emit", capture)

    def _fetch(_ip: str, _host: str) -> int:
        raise AssertionError("fetch_health must not run when no public IPv4")

    _patch_default_snapshot_io(monkeypatch, fetch=_fetch, addresses=["127.0.0.1"])
    monkeypatch.setattr(loop_mod.time, "time", lambda: 11_000_000.0)
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"), restart_desktop_fn=_noop_restart
    )
    _run(loop._tick())
    payloads = _ok_payloads(capture.events)
    assert len(payloads) == 1
    assert payloads[0]["health_checked"] is False
    assert payloads[0]["health_http_status"] is None


def test_probe_failed_clears_window_and_emits_no_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    capture = _CaptureEmit()
    monkeypatch.setattr(loop_mod, "_emit", capture)
    outcomes: list[object] = [200, TimeoutError("timed out"), 200]

    def _fetch(_ip: str, _host: str) -> int:
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return int(item)

    _patch_default_snapshot_io(monkeypatch, fetch=_fetch, addresses=["73.189.160.136"])
    clock = {"now": 12_000_000.0}
    monkeypatch.setattr(loop_mod.time, "time", lambda: clock["now"])
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"), restart_desktop_fn=_noop_restart
    )
    _run(loop._tick())
    clock["now"] += 30.0
    _run(loop._tick())
    clock["now"] += 30.0
    _run(loop._tick())
    payloads = _ok_payloads(capture.events)
    assert len(payloads) == 2
    assert all(p["health_checked"] is True for p in payloads)
    assert all(p["health_http_status"] == 200 for p in payloads)


def test_unpatched_restart_raises(
    monkeypatch: pytest.MonkeyPatch,
    grokbot_egress_emit_recorder: object,
) -> None:
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _base_snapshot()
    with pytest.raises(
        RuntimeError,
        match="unpatched grokbot egress watch reached _default_restart_desktop",
    ):
        _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=14_000_000.0))
    assert any(
        sig == "mcp.edge.grokbot_tunnel.stale"
        for sig, _payload in grokbot_egress_emit_recorder.events
    )


def test_desktop_down_while_watch_connected_emits_no_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = _CaptureEmit()
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        capture,
    )
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    snap = _healthy_snapshot(
        desktop_running=False,
        desktop_pid=None,
        watch_connected=True,
        tunnel_stale=False,
    )
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=13_000_000.0))
    assert _ok_payloads(capture.events) == []


def test_probe_failure_clears_window_when_same_tick_emit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnected log makes the stale emit run; a raised emit must not keep the window."""
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    capture = _CaptureEmit()
    phase = {"log": _CONNECTED_LOG, "fail_probe": False}

    async def _emit(signal: str, payload: dict[str, object], **_: object) -> None:
        if signal == "mcp.edge.grokbot_tunnel.stale":
            raise RuntimeError("event service down")
        capture.events.append((signal, payload))

    def _fetch(_ip: str, _host: str) -> int:
        if phase["fail_probe"]:
            raise TimeoutError("timed out")
        return 200

    _patch_default_snapshot_io(monkeypatch, fetch=_fetch, addresses=["73.189.160.136"])
    monkeypatch.setattr(loop_mod, "read_daemon_log", lambda _path: phase["log"])
    monkeypatch.setattr(loop_mod, "_emit", _emit)
    clock = {"now": 15_000_000.0}
    monkeypatch.setattr(loop_mod.time, "time", lambda: clock["now"])
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"), restart_desktop_fn=_noop_restart
    )
    _run(loop._tick())
    clock["now"] += 30.0
    phase["fail_probe"] = True
    dial_ms = int(clock["now"] * 1000) - 90_000
    from datetime import datetime

    dial_at = (
        datetime.fromtimestamp(dial_ms / 1000.0, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z"
    )
    phase["log"] = f"[user-computer-watch] {dial_at} dialing (attempt 1)\n"
    with pytest.raises(RuntimeError, match="event service down"):
        _run(loop._tick())
    phase["log"] = _CONNECTED_LOG
    phase["fail_probe"] = False
    clock["now"] += 30.0
    _run(loop._tick())
    payloads = _ok_payloads(capture.events)
    assert len(payloads) == 2
    assert all(p["health_checked"] is True for p in payloads)


def test_unstubbed_tick_refuses_desktop_pid_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    monkeypatch.setattr(
        loop_mod, "read_desktop_status", lambda _path: {"pid": 2_147_483_646}
    )
    monkeypatch.setattr(loop_mod, "read_daemon_log", lambda _path: _CONNECTED_LOG)
    loop = GrokbotEgressWatchLoop(workspace_root=Path("/tmp"))
    with pytest.raises(
        RuntimeError, match="unpatched grokbot egress watch reached desktop_pid_alive"
    ):
        _run(loop._tick())


def _hm1_false_connected_snapshot(**overrides: object) -> GrokbotEgressSnapshot:
    return _healthy_snapshot(hm1_tools_call_ok=False, **overrides)


def _tick_hm1_false(
    state: GrokbotEgressLoopState,
    *,
    now: float | None = None,
) -> object:
    snap = _hm1_false_connected_snapshot()
    t = now if now is not None else time.time()
    return decide_grokbot_tick(now=t, state=state, snapshot=snap)


def test_hm1_false_untrusted_event_rows_do_not_count() -> None:
    state = GrokbotEgressLoopState()
    snap = _hm1_false_connected_snapshot(event_rows_trusted=False)
    for _ in range(_HM1_FALSE_RECOVERY_CONSECUTIVE_N + 2):
        decision = decide_grokbot_tick(now=time.time(), state=state, snapshot=snap)
        assert decision.run_hm1_recovery_chain is False
    assert state.hm1_false_consecutive == 0


def test_hm1_false_n_minus_one_does_not_run_recovery_chain() -> None:
    state = GrokbotEgressLoopState()
    n = _HM1_FALSE_RECOVERY_CONSECUTIVE_N
    for _ in range(n - 1):
        decision = _tick_hm1_false(state)
        assert decision.run_hm1_recovery_chain is False
    assert state.hm1_false_consecutive == n - 1
    assert state.hm1_recovery_chain_ran is False


def test_hm1_false_nth_tick_runs_recovery_chain_once() -> None:
    state = GrokbotEgressLoopState()
    n = _HM1_FALSE_RECOVERY_CONSECUTIVE_N
    decision = None
    for _ in range(n):
        decision = _tick_hm1_false(state)
    assert decision is not None
    assert decision.run_hm1_recovery_chain is True
    assert state.hm1_recovery_chain_ran is True
    decision = _tick_hm1_false(state)
    assert decision.run_hm1_recovery_chain is False


def test_hm1_true_resets_false_streak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = GrokbotEgressLoopState()
    for _ in range(_HM1_FALSE_RECOVERY_CONSECUTIVE_N - 1):
        _tick_hm1_false(state)
    snap_ok = _healthy_snapshot(hm1_tools_call_ok=True)
    decide_grokbot_tick(now=time.time(), state=state, snapshot=snap_ok)
    assert state.hm1_false_consecutive == 0
    assert state.hm1_false_sustained_since is None
    assert state.hm1_recovery_chain_ran is False


def test_hm1_false_loop_invokes_recovery_io_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.grokbot_egress_watch_loop as loop_mod

    wakes: list[int] = []
    repoints: list[int] = []

    def _wake(**_kwargs: object) -> dict[str, object]:
        wakes.append(1)
        return {"ok": True}

    async def _probe() -> list[dict[str, object]]:
        return []

    def _repoint(_rows: list[dict[str, object]]) -> dict[str, object]:
        repoints.append(1)
        return {"wrote": False}

    monkeypatch.setattr(loop_mod, "keep_alive_grokbot_presence", _wake)
    monkeypatch.setattr(loop_mod, "probe_grok_agent_context", _probe)
    monkeypatch.setattr(loop_mod, "run_gc1_repoint", _repoint)
    capture = _CaptureEmit()
    monkeypatch.setattr(loop_mod, "_emit", capture)

    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("/tmp"), restart_desktop_fn=_noop_restart
    )
    snap = _hm1_false_connected_snapshot(desktop_running=False)
    t0 = 16_000_000.0
    for i in range(_HM1_FALSE_RECOVERY_CONSECUTIVE_N):
        _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0 + i * 30.0))
    assert len(repoints) == 1
    assert len(wakes) >= 1
    _run(_tick_with_snapshot(loop, snap, monkeypatch, now_s=t0 + 120.0))
    assert len(repoints) == 1


def test_hm1_false_backstop_rate_limited_second_episode() -> None:
    snap_false = _hm1_false_connected_snapshot(
        tunnel_stale=False,
        watch_connected=True,
        watch_disconnected_s=None,
    )
    snap_ok = _healthy_snapshot(hm1_tools_call_ok=True)
    state = GrokbotEgressLoopState()
    now = 17_000_000.0
    n = _HM1_FALSE_RECOVERY_CONSECUTIVE_N

    def _sustain_false(t_base: float) -> None:
        for i in range(n):
            decide_grokbot_tick(now=t_base + i * 30.0, state=state, snapshot=snap_false)

    _sustain_false(now)
    state.hm1_false_sustained_since = now - _LOADER_DESKTOP_BACKSTOP_S - 1.0
    first_restart = decide_grokbot_tick(
        now=now + 600.0, state=state, snapshot=snap_false
    )
    assert first_restart.restart_desktop is True
    restart_at = now + 600.0
    state.last_desktop_restart_at = restart_at

    decide_grokbot_tick(now=restart_at + 30.0, state=state, snapshot=snap_ok)
    assert state.hm1_false_consecutive == 0

    _sustain_false(restart_at + 60.0)
    state.hm1_false_sustained_since = (
        restart_at + 60.0 - _LOADER_DESKTOP_BACKSTOP_S - 1.0
    )
    blocked = decide_grokbot_tick(
        now=restart_at + 700.0, state=state, snapshot=snap_false
    )
    assert blocked.restart_desktop is False


def test_tunnel_stale_and_loader_stuck_unchanged_after_hm1_work() -> None:
    """Regression: prior tunnel/loader paths still decide restart as before."""
    state = GrokbotEgressLoopState()
    decision = decide_grokbot_tick(
        now=time.time(), state=state, snapshot=_base_snapshot()
    )
    assert decision.restart_desktop is True
    assert decision.emit_tunnel_stale is True

    stuck_snap = _healthy_snapshot(loader_stuck=True, loader_stuck_s=200.0)
    state2 = GrokbotEgressLoopState()
    first_loader = decide_grokbot_tick(
        now=time.time(), state=state2, snapshot=stuck_snap
    )
    assert first_loader.emit_loader_stuck is True
    state2.loader_stuck_first_at = time.time() - 300.0
    state2.loader_stuck_emitted_at = state2.loader_stuck_first_at
    before_backstop = decide_grokbot_tick(
        now=time.time(), state=state2, snapshot=stuck_snap
    )
    assert before_backstop.restart_desktop is False


def test_watch_ok_catalog_keys_match_factory() -> None:
    catalog = Path("docs/event-contracts.md").read_text(encoding="utf-8")
    row = next(
        line
        for line in catalog.splitlines()
        if line.startswith("| `mcp.edge.grokbot.watch.ok` |")
    )
    payload_cell = row.split("|")[2]
    catalog_keys = [
        part.strip().strip("`") for part in payload_cell.split(",") if part.strip()
    ]
    event = mcp_edge_grokbot_watch_ok(
        watch_connected=True,
        health_http_status=200,
        hm1_tools_call_ok=True,
        undici_idle_s=1.0,
        health_checked=True,
    )
    assert list(event.payload) == catalog_keys
