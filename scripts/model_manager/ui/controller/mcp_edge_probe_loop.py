"""Manage loop: public-vantage MCP /health plus standing-listener silence.

Started beside the other manage tick loops. Each pass resolves the MCP
hostname through DNS (not the hosts file), connects to a globally routable
IPv4 address, and reads ``/health``. It then asks which threads have been
calling ``mcp.agentbus.wait.called`` and emits one silence event per standing
listener that has gone quiet. Probe and query failures are events, not
exceptions that stop the loop. This loop does not restart mcp.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from deploy_identity.listener_liveness import LOOKBACK_MS, silent_listeners
from deploy_identity.mcp_edge_events import (
    mcp_edge_probe_failed,
    mcp_edge_probe_succeeded,
    mcp_listener_liveness_failed,
    mcp_listener_liveness_silent,
)
from deploy_identity.mcp_external_probe import (
    assess_external_probe,
    choose_public_ipv4,
    classify_probe_error,
    external_probe_hostname,
)
from universal_logging import get_logger

from scripts.model_manager.observation_event import _emit
from scripts.model_manager.ui.controller.mcp_edge_probe_io import (
    fetch_health,
    query_wait_rows,
    resolve_a_records,
)

logger = get_logger(__name__)

_INTERVAL_S = 60.0


class McpEdgeProbeLoop:
    """Periodic public-vantage probe and listener-silence check.

    ``start`` schedules ``_loop`` on the running event loop. ``stop``
    cancels it. One tick failure is logged and the next interval still
    runs. The loop never calls manage restart.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        interval_s: float = _INTERVAL_S,
    ) -> None:
        self._workspace_root = workspace_root
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Schedule the probe if it is not already running."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="mcp-edge-probe")

    async def stop(self) -> None:
        """Cancel the probe task and wait until it finishes."""
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
                logger.exception("mcp edge probe tick failed")
            await asyncio.sleep(self._interval_s)

    async def _tick(self) -> None:
        await self._probe_edge()
        await self._probe_listeners()

    async def _probe_edge(self) -> None:
        host = external_probe_hostname()
        addresses: list[str] = []
        error_class: str | None = None
        detail = ""
        http_status: int | None = None
        try:
            addresses = await asyncio.to_thread(resolve_a_records, host)
        except Exception as exc:
            error_class = classify_probe_error(exc)
            detail = str(exc)[:200]
        else:
            ip = choose_public_ipv4(addresses)
            if ip is not None:
                try:
                    http_status = await asyncio.to_thread(fetch_health, ip, host)
                except Exception as exc:
                    error_class = classify_probe_error(exc)
                    detail = str(exc)[:200]
        result = assess_external_probe(
            host=host,
            addresses=addresses,
            http_status=http_status,
            error_class=error_class,
            detail=detail,
        )
        if result.status == "succeeded" and result.ip is not None:
            event = mcp_edge_probe_succeeded(
                host=result.host,
                ip=result.ip,
                http_status=result.http_status or 200,
            )
        else:
            event = mcp_edge_probe_failed(
                host=result.host,
                ip=result.ip,
                error_class=result.error_class or "probe_error",
                detail=result.detail,
            )
        await _emit(
            event.signal,
            dict(event.payload),
            source="manage",
            role=event.role,
            scope=event.scope,
        )

    async def _probe_listeners(self) -> None:
        now_ms = int(time.time() * 1000)
        floor_ms = now_ms - LOOKBACK_MS
        try:
            rows = await asyncio.to_thread(
                query_wait_rows, self._workspace_root, floor_ms=floor_ms
            )
        except Exception as exc:
            event = mcp_listener_liveness_failed(
                error_class="query_failed",
                detail=str(exc)[:200],
            )
            await _emit(
                event.signal,
                dict(event.payload),
                source="manage",
                role=event.role,
                scope=event.scope,
            )
            return
        for row in silent_listeners(rows, now_ms=now_ms):
            event = mcp_listener_liveness_silent(
                thread=str(row["thread"]),
                age_ms=int(row["age_ms"]),
                calls=int(row["calls"]),
            )
            await _emit(
                event.signal,
                dict(event.payload),
                source="manage",
                role=event.role,
                scope=event.scope,
            )


__all__ = ["McpEdgeProbeLoop"]
