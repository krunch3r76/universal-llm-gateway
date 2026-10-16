"""Manage loop: Grok Bot sand egress tunnel and box MCP loader recovery."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from deploy_identity.mcp_edge_events import (
    mcp_edge_grokbot_loader_stuck,
    mcp_edge_grokbot_probe_failed,
    mcp_edge_grokbot_tunnel_stale,
)
from deploy_identity.mcp_external_probe import (
    choose_public_ipv4,
    external_probe_hostname,
)
from universal_logging import get_logger

from scripts.model_manager.observation_event import _emit
from scripts.model_manager.ui.controller.grokbot_egress_watch_io import (
    DEFAULT_DAEMON_LOG,
    DEFAULT_DESKTOP_BINARY,
    DEFAULT_DESKTOP_STATUS,
    build_snapshot_from_inputs,
    desktop_pid_alive,
    query_events_rows,
    read_daemon_log,
    read_desktop_status,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GrokbotEgressLoopState,
    GrokbotEgressSnapshot,
    decide_grokbot_tick,
)
from scripts.model_manager.ui.controller.mcp_edge_probe_io import (
    fetch_health,
    resolve_a_records,
)

logger = get_logger(__name__)

_INTERVAL_S = 30.0

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
"""


class GrokbotEgressWatchLoop:
    """Watch Grok Bot egress; emit observation events and restart desktop at most once per cap."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        interval_s: float = _INTERVAL_S,
        snapshot_fn: Callable[[], Awaitable[GrokbotEgressSnapshot]] | None = None,
        restart_desktop_fn: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._interval_s = interval_s
        self._snapshot_fn = snapshot_fn
        self._restart_desktop_fn = restart_desktop_fn
        self._state = GrokbotEgressLoopState()
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
        try:
            host = external_probe_hostname()
            addresses = await asyncio.to_thread(resolve_a_records, host)
            ip = choose_public_ipv4(addresses)
            if ip is not None:
                health_status = await asyncio.to_thread(fetch_health, ip, host)
        except Exception as exc:
            logger.debug("grokbot egress health probe skipped: %s", exc)
        floor_ms = now_ms - 900_000
        try:
            rows = await asyncio.to_thread(
                query_events_rows,
                self._workspace_root,
                _EVENT_SQL,
                floor_ms,
            )
        except Exception as exc:
            logger.warning("grokbot egress event query failed: %s", exc)
            rows = []
        return build_snapshot_from_inputs(
            now_ms=now_ms,
            desktop_running=running,
            desktop_pid=pid,
            daemon_log=daemon_log,
            health_http_status=health_status,
            event_rows=rows,
        )

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

    async def _tick(self) -> None:
        snapshot = (
            await self._snapshot_fn()
            if self._snapshot_fn is not None
            else await self._default_snapshot()
        )
        now = time.time()
        decision = decide_grokbot_tick(
            now=now, state=self._state, snapshot=snapshot
        )

        if decision.emit_probe_failed:
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


__all__ = ["GrokbotEgressWatchLoop"]
