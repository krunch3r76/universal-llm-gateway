"""Headless fleet orchestration — Sync+Restart All / Rebuild+Deploy All.

Lifted out of view/widgets/topology_panel.py so any UI (TUI now, web/native later)
invokes one operation and merely renders progress through a FleetProgressSink. Zero
Textual imports: this is Model-layer code. Progress is reported two ways — the
fine-grained per-node log/status via the sink (synchronous, in-process) and the
coarse fleet.* observation events (push, already consumed by the Event Service).

Local-phase helpers live in fleet_local.py and remote-node helpers in
fleet_remote.py (free functions taking sink/ctl/root) so each module stays ≤300 SLOC.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from scripts.model_manager.observation_event import (
    emit_fleet_operation_completed,
    emit_fleet_operation_started,
)

from .restart_window_ctl import clear_fleet_windows, open_fleet_window

from .fleet_local import (
    parallel_build,
    restart_local_services,
    stop_local_services,
    wait_event_service_healthy,
)
from .fleet_remote import (
    _MASTER_ROW_KEY,
    _parse_remote_targets,
    deploy_remotes_parallel,
    stop_remote_before_operation,
    verify_relay_connection,
)
from .topology import list_remotes

if TYPE_CHECKING:
    from pathlib import Path

    from .service_ctl.core import ServiceController


@runtime_checkable
class FleetProgressSink(Protocol):
    """View-side progress renderer the orchestrator emits through."""

    def line(self, node: str, text: str) -> None: ...
    def status(self, node: str, text: str) -> None: ...
    def focus(self, node: str) -> None: ...


class NullFleetSink:
    """Sink that drops all progress — for headless (agent) invocations."""

    def line(self, node: str, text: str) -> None: ...
    def status(self, node: str, text: str) -> None: ...
    def focus(self, node: str) -> None: ...


@dataclass(slots=True, kw_only=True)
class FleetResult:
    """Outcome of a fleet operation."""

    operation: str
    build: bool
    success: bool
    duration_s: float
    failures: list[str] = field(default_factory=list)


class FleetOrchestrator:
    """Headless stop→(build)→restart→verify sequencing across the local node + relays.

    One per fleet operation invocation. Holds no Textual state; emits progress through
    the injected FleetProgressSink and the fleet.* event vocabulary. Registers the
    'fleet_deploy' activity on the shutdown gate for the operation's lifetime so a
    quit (TUI) or a concurrent restart (agent) is gated regardless of caller.
    """

    def __init__(
        self,
        *,
        ctl: ServiceController,
        root: Path,
        sink: FleetProgressSink,
    ) -> None:
        self._ctl = ctl
        self._root = root
        self._sink = sink

    async def sync_restart_all(self, *, build: bool, scope: str) -> FleetResult:
        """Run the full fleet operation (stop → optional build → restart → verify)."""
        ctl = self._ctl
        mk = _MASTER_ROW_KEY
        operation = "rebuild_deploy" if build else "sync_restart"
        remotes = [h for h, _ in _parse_remote_targets(list_remotes())]
        t0 = time.monotonic()
        failures: list[str] = []

        ctl.shutdown_gate.set_activity("fleet_deploy", True)
        self._sink.focus(mk)
        try:
            if not await wait_event_service_healthy(ctl, timeout=3):
                self._sink.line(mk, "  ○ starting event_service (for observability)")
                await ctl.start_event_service()
                await wait_event_service_healthy(ctl, timeout=10)

            await emit_fleet_operation_started(
                operation=operation, build=build, scope=scope, remotes=remotes
            )

            await open_fleet_window(
                ctl.restart_intent_store,
                reason=f"fleet {operation}",
            )

            stopped_ok = await self._stop_fleet_before_operation(
                "rebuild" if build else "restart"
            )
            if build:
                await self._run_build_path(scope, stopped_ok, failures)
            else:
                await self._run_restart_path(scope, stopped_ok, failures)
        finally:
            # Emit/clear may await; clear activity in an inner finally so a cancel
            # or hung emit cannot leave fleet_deploy pinned on the quit drain.
            elapsed = time.monotonic() - t0
            try:
                await emit_fleet_operation_completed(
                    operation=operation,
                    build=build,
                    success=not failures,
                    duration_s=elapsed,
                    failures=failures,
                )
                await clear_fleet_windows(
                    ctl.restart_intent_store,
                    reason="fleet.operation.completed",
                )
            finally:
                ctl.shutdown_gate.set_activity("fleet_deploy", False)

        return FleetResult(
            operation=operation,
            build=build,
            success=not failures,
            duration_s=elapsed,
            failures=failures,
        )

    async def _run_build_path(
        self, scope: str, stopped_ok: bool, failures: list[str]
    ) -> None:
        """Rebuild+Deploy branch: build images, restart locals, verify relays."""
        mk = _MASTER_ROW_KEY
        if not stopped_ok:
            failures.append("stop_before_build")
            self._sink.focus(mk)
            self._sink.line(
                mk,
                "⚠ Fleet stop phase failed; aborted rebuild before any new image restart.",
            )
            return

        local_build_ok, remote_results = await parallel_build(
            self._ctl, self._root, self._sink, scope
        )
        for hostname, ok in remote_results.items():
            if not ok:
                failures.append(f"remote_build:{hostname}")
        if not local_build_ok:
            failures.append("local_build")
            self._sink.focus(mk)
            self._sink.line(mk, "⚠ Local build failed; skipped local restart.")
            return

        local_restart_ok = await restart_local_services(
            self._ctl,
            self._root,
            self._sink,
            rebuild_supporting_services=True,
            already_stopped=True,
        )
        if local_restart_ok:
            for hostname, ok in remote_results.items():
                if ok and not await verify_relay_connection(hostname, sink=self._sink):
                    failures.append(f"remote_verify:{hostname}")
        else:
            failures.append("local_restart")
            self._sink.focus(mk)
            self._sink.line(
                mk,
                "⚠ Localhost restart failed after build; skipped relay verification.",
            )

    async def _run_restart_path(
        self, scope: str, stopped_ok: bool, failures: list[str]
    ) -> None:
        """Sync+Restart branch: restart locals, then sync/restart remotes."""
        mk = _MASTER_ROW_KEY
        if not stopped_ok:
            failures.append("stop_before_restart")
            self._sink.focus(mk)
            self._sink.line(
                mk,
                "⚠ Fleet stop phase failed; aborted restart before any service start.",
            )
            return

        local_restart_ok = await restart_local_services(
            self._ctl,
            self._root,
            self._sink,
            rebuild_supporting_services=False,
            already_stopped=True,
        )
        if not local_restart_ok:
            failures.append("local_restart")
            self._sink.focus(mk)
            self._sink.line(
                mk, "⚠ Localhost restart failed; skipped remote sync/restart."
            )
            return

        remote_results = await deploy_remotes_parallel(
            build=False, scope=scope, sink=self._sink, root=self._root
        )
        for hostname, ok in remote_results.items():
            if not ok:
                failures.append(f"remote_restart:{hostname}")

    async def _stop_fleet_before_operation(self, operation: str) -> bool:
        """Bring local and remote edge processes down before fleet operations."""
        targets = _parse_remote_targets(list_remotes())
        results: dict[str, bool] = {}

        self._sink.status(_MASTER_ROW_KEY, f"⟳ stopping before {operation}...")
        for hostname, _ in targets:
            self._sink.status(hostname, f"⟳ stopping before {operation}...")

        async with asyncio.TaskGroup() as tg:
            local_stop = tg.create_task(self._stop_local_before_operation(operation))
            for hostname, address in targets:
                tg.create_task(
                    stop_remote_before_operation(
                        hostname=hostname,
                        address=address,
                        operation=operation,
                        results=results,
                        sink=self._sink,
                    )
                )

        return local_stop.result() and all(results.values())

    async def _stop_local_before_operation(self, operation: str) -> bool:
        """Stop local services and fail closed if anything stays up."""
        mk = _MASTER_ROW_KEY
        self._sink.focus(mk)
        self._sink.line(mk, f"Stopping local services before {operation}...")
        failures = await stop_local_services(self._ctl, self._sink)
        if failures:
            self._sink.status(mk, "✗ stop failed")
            self._sink.line(mk, f"Stop failed: {', '.join(failures)}")
            return False
        self._sink.status(mk, "○ stopped, next phase pending")
        self._sink.line(mk, f"All local services stopped before {operation}.")
        return True
