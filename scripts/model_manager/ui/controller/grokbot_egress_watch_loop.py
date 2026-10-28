"""Manage loop: Grok Bot sand egress tunnel and box MCP loader recovery."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from deploy_identity.mcp_edge_events import (
    mcp_edge_grokbot_context_hop_due,
    mcp_edge_grokbot_context_hop_notify_failed,
    mcp_edge_grokbot_loader_stuck,
    mcp_edge_grokbot_probe_failed,
    mcp_edge_grokbot_tunnel_stale,
    mcp_edge_grokbot_watch_ok,
)
from deploy_identity.mcp_external_probe import (
    choose_public_ipv4,
    external_probe_hostname,
)
from implement_admission.impresario_events import ImpresarioHopNoticed
from universal_logging import get_logger

from scripts.model_manager.observation_event import _emit
from scripts.model_manager.ui.controller.grokbot_context_hop_io import (
    probe_grok_agent_context,
    reply_context_hop,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_io import (
    DEFAULT_DAEMON_LOG,
    DEFAULT_DESKTOP_BINARY,
    DEFAULT_DESKTOP_STATUS,
    DEFAULT_HOP_COOLDOWN,
    build_snapshot_from_inputs,
    desktop_pid_alive,
    keep_alive_grokbot_presence,
    load_hop_cooldown_journal,
    query_events_rows,
    read_daemon_log,
    read_desktop_status,
    save_hop_cooldown_journal,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    HOP_THREAD_ID,
    GrokbotEgressLoopState,
    GrokbotEgressSnapshot,
    GrokbotTickDecision,
    decide_context_hop,
    decide_grokbot_tick,
    rebuild_hop_state,
    record_hop_notified,
)
from scripts.model_manager.ui.controller.grokbot_seat_repoint import (
    run_gc1_repoint,
    run_impresario_repoint,
)
from scripts.model_manager.ui.controller.mcp_edge_probe_io import (
    fetch_health,
    resolve_a_records,
)

logger = get_logger(__name__)

_INTERVAL_S = 30.0
_WATCH_OK_INTERVAL_S = 120.0

_EVENT_SQL = """
SELECT ts_unix_ms, signal, payload
FROM events
WHERE ts_unix_ms >= ?
  AND signal IN (
    'mcp.request.completed',
    'mcp.edge.request.observed',
    'mcp.transport.request.started',
    'mcp.transport.request.completed',
    'mcp.transport.request.failed'
  )
ORDER BY ts_unix_ms DESC
"""

_EVENT_QUERY_ROW_LIMIT = 1000


class GrokbotEgressWatchLoop:
    """Watch Grok Bot egress; emit observation events and restart desktop at most once per cap."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        interval_s: float = _INTERVAL_S,
        snapshot_fn: Callable[[], Awaitable[GrokbotEgressSnapshot]] | None = None,
        restart_desktop_fn: Callable[[], Awaitable[None]] | None = None,
        hop_cooldown_path: Path | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._interval_s = interval_s
        self._snapshot_fn = snapshot_fn
        self._restart_desktop_fn = restart_desktop_fn
        self._hop_cooldown_path = hop_cooldown_path or DEFAULT_HOP_COOLDOWN
        self._state = GrokbotEgressLoopState()
        self._hop_state = rebuild_hop_state(
            load_hop_cooldown_journal(self._hop_cooldown_path)
        )
        self._last_watch_ok_emitted_at: float | None = None
        self._health_probe_failed = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="grokbot-egress-watch")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("grokbot egress watch tick failed")
            await asyncio.sleep(self._interval_s)

    async def _default_snapshot(self) -> GrokbotEgressSnapshot:
        now_ms = int(time.time() * 1000)
        status = await asyncio.to_thread(read_desktop_status, DEFAULT_DESKTOP_STATUS)
        running, pid = desktop_pid_alive(status)
        daemon_log = await asyncio.to_thread(read_daemon_log, DEFAULT_DAEMON_LOG)
        health_status: int | None = None
        self._health_probe_failed = False
        try:
            host = external_probe_hostname()
            if host:
                addresses = await asyncio.to_thread(resolve_a_records, host)
                ip = choose_public_ipv4(addresses)
                if ip is not None:
                    health_status = await asyncio.to_thread(fetch_health, ip, host)
        except Exception as exc:
            self._health_probe_failed = True
            logger.debug("grokbot egress health probe failed: %s", exc)
        floor_ms = now_ms - 900_000
        event_rows_trusted = True
        try:
            rows = await asyncio.to_thread(
                query_events_rows,
                self._workspace_root,
                _EVENT_SQL,
                floor_ms,
                limit=_EVENT_QUERY_ROW_LIMIT,
            )
        except Exception as exc:
            logger.warning("grokbot egress event query failed: %s", exc)
            rows = []
            event_rows_trusted = False
        else:
            if len(rows) >= _EVENT_QUERY_ROW_LIMIT:
                logger.warning(
                    "grokbot egress event query hit row limit (%s); "
                    "newest rows retained (ORDER BY ts_unix_ms DESC)",
                    _EVENT_QUERY_ROW_LIMIT,
                )
                event_rows_trusted = False
        snap = build_snapshot_from_inputs(
            now_ms=now_ms,
            desktop_running=running,
            desktop_pid=pid,
            daemon_log=daemon_log,
            health_http_status=health_status,
            event_rows=rows,
        )
        if not event_rows_trusted:
            return replace(snap, event_rows_trusted=False)
        return snap

    async def _default_restart_desktop(self) -> None:
        binary = DEFAULT_DESKTOP_BINARY
        if not binary.is_file():
            logger.error("grokbot desktop binary missing: %s", binary)
            return
        status = read_desktop_status(DEFAULT_DESKTOP_STATUS)
        running, pid = desktop_pid_alive(status)
        if running and pid is not None:
            try:
                import os
                import signal

                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(2.0)
            except OSError as exc:
                logger.warning("grokbot desktop SIGTERM failed pid=%s: %s", pid, exc)
        proc = await asyncio.create_subprocess_exec(str(binary))
        logger.info("grokbot desktop restart launched pid=%s", proc.pid)

    @staticmethod
    def _tick_had_failure_signal(
        decision: GrokbotTickDecision, snapshot: GrokbotEgressSnapshot
    ) -> bool:
        if decision.emit_probe_failed:
            return True
        if (
            snapshot.health_http_status is not None
            and snapshot.health_http_status != 200
        ):
            return True
        return decision.emit_tunnel_stale or decision.emit_loader_stuck

    @staticmethod
    def _tick_healthy_for_watch_ok(snapshot: GrokbotEgressSnapshot) -> bool:
        return (
            snapshot.daemon_log_readable
            and snapshot.desktop_running
            and snapshot.watch_connected
            and not snapshot.tunnel_stale
            and not snapshot.loader_stuck
        )

    async def _maybe_emit_watch_ok(
        self,
        *,
        now: float,
        decision: GrokbotTickDecision,
        snapshot: GrokbotEgressSnapshot,
    ) -> None:
        if self._health_probe_failed or self._tick_had_failure_signal(
            decision, snapshot
        ):
            self._last_watch_ok_emitted_at = None
            return
        if not self._tick_healthy_for_watch_ok(snapshot):
            return
        last = self._last_watch_ok_emitted_at
        if last is not None and (now - last) < _WATCH_OK_INTERVAL_S:
            return
        event = mcp_edge_grokbot_watch_ok(
            watch_connected=snapshot.watch_connected,
            health_http_status=snapshot.health_http_status,
            hm1_tools_call_ok=snapshot.hm1_tools_call_ok,
            undici_idle_s=snapshot.undici_idle_s,
            health_checked=snapshot.health_http_status is not None,
        )
        await _emit(
            event.signal,
            dict(event.payload),
            source="manage",
            role=event.role,
            scope=event.scope,
        )
        self._last_watch_ok_emitted_at = now

    async def _tick(self) -> None:
        self._health_probe_failed = False
        snapshot = (
            await self._snapshot_fn()
            if self._snapshot_fn is not None
            else await self._default_snapshot()
        )
        if snapshot.desktop_running:
            await asyncio.to_thread(keep_alive_grokbot_presence)
        now = time.time()
        decision = decide_grokbot_tick(now=now, state=self._state, snapshot=snapshot)

        if decision.run_hm1_recovery_chain:
            await self._run_hm1_recovery_chain()

        if self._health_probe_failed:
            self._last_watch_ok_emitted_at = None

        if decision.emit_probe_failed:
            self._last_watch_ok_emitted_at = None
            event = mcp_edge_grokbot_probe_failed(
                error_class=decision.probe_error_class,
                detail=decision.probe_detail,
            )
            await _emit(
                event.signal,
                dict(event.payload),
                source="manage",
                role=event.role,
                scope=event.scope,
            )
            return

        if decision.warn_undici_gap:
            logger.warning(
                "grokbot egress: watch connected but gc1 undici idle %.0fs",
                snapshot.undici_idle_s or 0.0,
            )

        if decision.emit_tunnel_stale:
            event = mcp_edge_grokbot_tunnel_stale(
                disconnected_s=snapshot.watch_disconnected_s or 0.0,
                desktop_pid=snapshot.desktop_pid,
            )
            await _emit(
                event.signal,
                dict(event.payload),
                source="manage",
                role=event.role,
                scope=event.scope,
            )

        if decision.emit_loader_stuck:
            event = mcp_edge_grokbot_loader_stuck(
                since_tools_list_s=snapshot.loader_stuck_s or 0.0,
                gc1_client_ip="172.18.0.1",
            )
            await _emit(
                event.signal,
                dict(event.payload),
                source="manage",
                role=event.role,
                scope=event.scope,
            )

        if decision.restart_desktop:
            restart = self._restart_desktop_fn or self._default_restart_desktop
            await restart()

        await self._maybe_emit_watch_ok(now=now, decision=decision, snapshot=snapshot)
        if snapshot.desktop_running:
            try:
                await self._context_hop(
                    now=now, now_ms=int(now * 1000), snapshot=snapshot
                )
            except Exception:
                logger.exception("grokbot context hop tick failed")

    async def _run_hm1_recovery_chain(self) -> None:
        """Wake Grok Bot presence and repoint gc1 when hm1 tools/call stays absent."""
        try:
            await asyncio.to_thread(keep_alive_grokbot_presence)
            rows = await probe_grok_agent_context()
            await asyncio.to_thread(run_gc1_repoint, rows)
        except Exception:
            logger.exception("grokbot hm1-false recovery chain failed")

    async def _context_hop(
        self, *, now: float, now_ms: int, snapshot: GrokbotEgressSnapshot
    ) -> None:
        """Roster read and one combined notice. Skipped when desktop is down."""
        rows = await probe_grok_agent_context()
        try:
            run_gc1_repoint(rows)
        except Exception:
            logger.exception("grokbot gc1 seat repoint failed")
        try:
            await asyncio.to_thread(run_impresario_repoint, rows)
        except Exception:
            logger.exception("grokbot impresario seat repoint failed")
        notice = decide_context_hop(
            rows=rows,
            now=now,
            now_ms=now_ms,
            state=self._hop_state,
            open_gc1_bus_turn=snapshot.open_gc1_bus_turn,
            in_flight_gc1_mcp=snapshot.in_flight_gc1_mcp,
        )
        if notice is None:
            return
        due = mcp_edge_grokbot_context_hop_due(
            names=notice.names, thread_id=HOP_THREAD_ID
        )
        await _emit(
            due.signal,
            dict(due.payload),
            source="manage",
            role=due.role,
            scope=due.scope,
        )
        body = "context-hop-due: " + ", ".join(notice.names)
        try:
            await reply_context_hop(subject=notice.subject, body=body, after_turn=0)
        except Exception as exc:
            failed = mcp_edge_grokbot_context_hop_notify_failed(
                error_class=type(exc).__name__,
                detail=str(exc),
            )
            await _emit(
                failed.signal,
                dict(failed.payload),
                source="manage",
                role=failed.role,
                scope=failed.scope,
            )
            return
        record_hop_notified(self._hop_state, notice.agent_ids, now=now)
        save_hop_cooldown_journal(
            self._hop_cooldown_path, self._hop_state.cooldown_until
        )
        noticed = ImpresarioHopNoticed(
            house_root=HOP_THREAD_ID,
            hop_name=notice.subject,
            detail={"names": notice.names, "agent_ids": notice.agent_ids},
        )
        await _emit(
            noticed.signal,
            dict(noticed.payload),
            source="manage",
            role=noticed.role,
            scope=noticed.scope,
        )


__all__ = ["GrokbotEgressWatchLoop"]
