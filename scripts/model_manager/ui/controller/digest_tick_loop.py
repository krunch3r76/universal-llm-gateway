"""Manage-owned periodic digest job ticker.

Advances ``ENQUEUED`` digest jobs via ``asyncio.to_thread(tick_jobs)`` while
``./manage`` is up and cortex-api is healthy. No cron/systemd and no background
loop inside cortex-api.

Ops note (F2c): ticks skip during cortex-api blips even though the shared DB
could be reached directly — charter binds tick authority to cortex-api health.
CDP extract/verify CPU runs in the manage TUI process (Pillar-3 dispatch bypass
for v0).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from universal_logging import get_logger

from scripts.model_manager import observation_event as events
from scripts.model_manager.ui.controller.service_config import build_service_env
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceState, ServiceStatus

logger = get_logger(__name__)

_DEFAULT_TICK_INTERVAL_S = 30.0
_DEFAULT_LIMIT = 1
_ACTIVITY = "digest_tick"

_EXTRA_DIGEST_KEYS = frozenset(
    {
        "PROJECT_ASK_URL",
        "CORTEX_DB_PATH",
        "TODOS_DB_PATH",
        "PYTHONPATH",
    }
)


def ensure_digest_tick_env(workspace_root: Path) -> bool:
    """Overwrite digest-critical env from ``build_service_env``; fail-closed unless cdp."""
    merged = build_service_env(workspace_root)
    libs_path = str(workspace_root / "libs")
    merged["PYTHONPATH"] = (
        f"{libs_path}:{merged['PYTHONPATH']}" if merged.get("PYTHONPATH") else libs_path
    )
    for key, value in merged.items():
        if key.startswith("CORTEX_DIGEST_") or key in _EXTRA_DIGEST_KEYS:
            os.environ[key] = value
    backend = os.environ.get("CORTEX_DIGEST_EXTRACT_BACKEND", "").strip().lower()
    return backend == "cdp"


class DigestTickLoop:
    """Async supervisor: periodic ``tick_jobs`` while manage is mounted."""

    def __init__(
        self,
        *,
        service_state: ServiceState,
        shutdown_gate: ManageShutdownGate,
        workspace_root: Path,
        tick_interval_s: float = _DEFAULT_TICK_INTERVAL_S,
        limit: int = _DEFAULT_LIMIT,
    ) -> None:
        self._service_state = service_state
        self._shutdown_gate = shutdown_gate
        self._workspace_root = workspace_root
        self._tick_interval_s = tick_interval_s
        self._limit = limit
        self._loop_task: asyncio.Task[None] | None = None
        self._tick_task: asyncio.Task[object] | None = None
        self._env_ready = False

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._loop_task = asyncio.create_task(self._run_loop())
        await events.emit_manage_digest_tick_started()

    async def stop(self) -> None:
        loop_task = self._loop_task
        if loop_task is None:
            return
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        tick_task = self._tick_task
        if tick_task is not None:
            await tick_task
        self._loop_task = None
        self._tick_task = None
        await events.emit_manage_digest_tick_stopped()

    async def _run_loop(self) -> None:
        from cortex_store.digest_jobs import tick_jobs

        try:
            while True:
                if not self._env_ready:
                    if not ensure_digest_tick_env(self._workspace_root):
                        await events.emit_manage_digest_tick_skipped(
                            reason="extract_backend_not_cdp"
                        )
                        await asyncio.sleep(self._tick_interval_s)
                        continue
                    self._env_ready = True

                cortex = self._service_state.check_cortex_api()
                if cortex.status != ServiceStatus.RUNNING:
                    await events.emit_manage_digest_tick_skipped(
                        reason="cortex_api_unhealthy"
                    )
                    await asyncio.sleep(self._tick_interval_s)
                    continue

                self._shutdown_gate.set_activity(_ACTIVITY, True)
                try:
                    self._tick_task = asyncio.create_task(
                        asyncio.to_thread(tick_jobs, limit=self._limit)
                    )
                    result = await self._tick_task
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — tick errors are non-fatal
                    logger.exception("digest tick failed")
                    await events.emit_manage_digest_tick_error(reason=str(exc))
                else:
                    count = int(result.get("count") or 0) if isinstance(result, dict) else 0
                    if count > 0:
                        status = str(result.get("status", "ok")) if isinstance(result, dict) else "ok"
                        await events.emit_manage_digest_tick_completed(
                            count=count,
                            status=status,
                        )
                finally:
                    self._tick_task = None
                    self._shutdown_gate.set_activity(_ACTIVITY, False)

                await asyncio.sleep(self._tick_interval_s)
        except asyncio.CancelledError:
            self._shutdown_gate.set_activity(_ACTIVITY, False)
            raise
