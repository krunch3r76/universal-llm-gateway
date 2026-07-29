"""WakeConsumer — single-task drain of dirty roots into per-root charter passes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client
from .enrollment_filter import refresh_migrated_roots_cache
from .env_snapshot import build_env_snapshot
from .giw_live_hold import build_tick_env_snapshot
from .harvest import harvest_completed_windows
from .kernel import hold as tick_hold
from .kernel.host import _admission_mode_from_env
from .kernel_tick import apply_kernel_tick_for_root
from .wake_hub import WakeDirtySet, WakeRootMapper

if TYPE_CHECKING:
    from .kernel.host import CharterRunnerTickLoop

logger = get_logger(__name__)

WakeSource = Literal["wake", "floor"]


@dataclass
class RootPassOutcome:
    root_id: str
    admitted: bool
    old_decision_label: str
    skipped_reason: str | None
    fire_attempt_outcome: str | None = None
    fire_attempt_reason: str | None = None


@dataclass
class BatchOutcome:
    wake_source: WakeSource
    roots_processed: int
    admitted: int
    in_flight: int
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    closed_attributions: list[str] = field(default_factory=list)
    old_decisions: dict[str, str] = field(default_factory=dict)


async def run_root_pass(
    root_id: str,
    *,
    tick_loop: CharterRunnerTickLoop,
    kernel_env: Any,
    pass_source: WakeSource | None = None,
) -> RootPassOutcome:
    """One root: harvest → kernel tick → skip side-effects."""
    from .kernel.skip_side_effects import apply_skip_side_effects

    turns = await bus_client.fetch_turns(root_id)
    admission_mode = _admission_mode_from_env(kernel_env, root_id)
    await harvest_completed_windows(root_id, turns, admission_mode=admission_mode)
    kernel_outcome = await apply_kernel_tick_for_root(
        root_id,
        turns,
        caps=tick_loop._caps,
        workspace_root=tick_loop._workspace_root,
        env=kernel_env,
        on_admit=tick_loop._on_admit,
        admission_mode=admission_mode,
        pass_source=pass_source,
    )
    label = kernel_outcome.old_decision_label
    skipped_by_reason: dict[str, int] = {}
    state_closes = 0
    try:
        await apply_skip_side_effects(
            root_id=root_id,
            turns=turns,
            skipped_reason=kernel_outcome.skipped_reason,
            old_decision_label=label,
            admitted=kernel_outcome.admitted,
            state_closes_this_tick=state_closes,
            skipped_by_reason=skipped_by_reason,
            caps=tick_loop._caps,
            fire_attempt_outcome=kernel_outcome.fire_attempt_outcome,
            fire_attempt_reason=kernel_outcome.fire_attempt_reason,
        )
    except Exception:  # noqa: BLE001 — skip/SOS must not abort pass
        logger.exception("charter-runner skip side-effects failed root=%s", root_id)
    return RootPassOutcome(
        root_id=root_id,
        admitted=kernel_outcome.admitted,
        old_decision_label=label,
        skipped_reason=kernel_outcome.skipped_reason,
        fire_attempt_outcome=kernel_outcome.fire_attempt_outcome,
        fire_attempt_reason=kernel_outcome.fire_attempt_reason,
    )


async def run_roots_batch(
    root_ids: list[str],
    *,
    tick_loop: CharterRunnerTickLoop,
    wake_source: WakeSource,
) -> BatchOutcome:
    """Shared floor/wake batch: env build + per-root passes + floor postamble."""
    refresh_migrated_roots_cache()
    env_snapshot = await build_tick_env_snapshot()
    kernel_env = await build_env_snapshot(
        root_ids=root_ids,
        env_half=env_snapshot,
    )
    admitted = 0
    in_flight = 0
    skipped_by_reason: dict[str, int] = {}
    closed_attributions: list[str] = []
    old_decisions: dict[str, str] = {}
    for root_id in root_ids:
        if not root_id:
            continue
        outcome = await run_root_pass(
            root_id,
            tick_loop=tick_loop,
            kernel_env=kernel_env,
            pass_source=wake_source,
        )
        old_decisions[root_id] = outcome.old_decision_label
        if outcome.admitted:
            admitted += 1
        if outcome.old_decision_label == "NOOP":
            in_flight += 1
        if outcome.skipped_reason:
            skipped_by_reason[outcome.skipped_reason] = (
                skipped_by_reason.get(outcome.skipped_reason, 0) + 1
            )
    if wake_source == "floor":
        await _floor_postamble(
            tick_loop,
            root_ids=root_ids,
            kernel_env=kernel_env,
            old_decisions=old_decisions,
            admitted=admitted,
            in_flight=in_flight,
            skipped_by_reason=skipped_by_reason,
            closed_attributions=closed_attributions,
        )
    else:
        await events.emit_manage_charter_tick_scanned(
            roots=len(root_ids),
            admitted=admitted,
            skipped_by_reason=skipped_by_reason or None,
        )
    return BatchOutcome(
        wake_source=wake_source,
        roots_processed=len(root_ids),
        admitted=admitted,
        in_flight=in_flight,
        skipped_by_reason=skipped_by_reason,
        closed_attributions=closed_attributions,
        old_decisions=old_decisions,
    )


async def _floor_postamble(
    tick_loop: CharterRunnerTickLoop,
    *,
    root_ids: list[str],
    kernel_env: Any,
    old_decisions: dict[str, str],
    admitted: int,
    in_flight: int,
    skipped_by_reason: dict[str, int],
    closed_attributions: list[str],
) -> None:
    try:
        from .kernel.shadow import record_shadow_pass
        from .telemetry import emit_shadow_diff, emit_shadow_ledger_starved

        shadow = record_shadow_pass(old_decisions, env=kernel_env)
        if shadow.starved:
            await emit_shadow_ledger_starved(
                reason=shadow.starve_reason or "ledger_empty",
                bus_roots=shadow.bus_roots,
            )
        for row in shadow.rows:
            if row.get("starved"):
                continue
            await emit_shadow_diff(
                root=row["root"],
                old_decision=row["old_decision"],
                kernel_transition=row["kernel_transition"],
                classification=row["classification"],
            )
    except Exception:  # noqa: BLE001 — shadow must not abort tick
        logger.exception("charter-runner shadow pass failed")
    await events.emit_manage_charter_tick_scanned(
        roots=len(root_ids),
        admitted=admitted,
        skipped_by_reason=skipped_by_reason or None,
    )
    try:
        from pager_notify import notify_tick_complete, scan_operator_bus_turns

        await notify_tick_complete(
            roots=len(root_ids),
            in_flight=in_flight,
            admitted=admitted,
            skipped_by_reason=skipped_by_reason or None,
            closed_attributions=closed_attributions or None,
        )
        await scan_operator_bus_turns()
    except Exception:  # noqa: BLE001 — pager must not abort tick
        logger.exception("charter-runner pager notify failed")
    try:
        from . import tick_friction_reconcile as _tick_reconcile

        roots = [{"id": rid} for rid in root_ids]
        await _tick_reconcile.reconcile_enrolled_roots_on_tick(roots)
    except Exception:  # noqa: BLE001 — reconcile must not abort tick
        logger.exception("charter-runner tick-scan friction reconcile failed")


ServicesHealthy = Callable[[], bool]


@dataclass
class WakeConsumer:
    """Single consumer: dirty-set drain + reconcile-interval floor full-roster pass."""

    tick_loop: CharterRunnerTickLoop
    dirty: WakeDirtySet
    mapper: WakeRootMapper
    floor_interval_s: float
    services_healthy: ServicesHealthy
    _task: asyncio.Task[None] | None = None
    _shutdown_gate_activity: Callable[[bool], None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def enqueue_full_roster(self) -> None:
        enrolled = await self.mapper.refresh_enrolled()
        self.dirty.enqueue_many(enrolled)

    async def _run_loop(self) -> None:
        activity = self._shutdown_gate_activity
        try:
            while True:
                held = tick_hold.read_hold()
                if held is not None:
                    await asyncio.sleep(self.floor_interval_s)
                    continue
                if not self.services_healthy():
                    await asyncio.sleep(self.floor_interval_s)
                    continue
                triggered = await self.dirty.wait(timeout=self.floor_interval_s)
                if tick_hold.read_hold() is not None:
                    continue
                if triggered:
                    batch_items = self.dirty.drain()
                    if not batch_items:
                        continue
                    root_ids = [root for root, _coalesced in batch_items if root]
                    if not root_ids:
                        continue
                    if activity is not None:
                        activity(True)
                    try:
                        await run_roots_batch(
                            root_ids,
                            tick_loop=self.tick_loop,
                            wake_source="wake",
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — pass errors are non-fatal
                        logger.exception("charter-runner wake consumer pass failed")
                        await events.emit_manage_charter_tick_error(reason=str(exc))
                    finally:
                        if activity is not None:
                            activity(False)
                    continue
                if activity is not None:
                    activity(True)
                try:
                    enrolled = await self.mapper.refresh_enrolled()
                    root_ids = sorted(enrolled)
                    if root_ids:
                        await run_roots_batch(
                            root_ids,
                            tick_loop=self.tick_loop,
                            wake_source="floor",
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — pass errors are non-fatal
                    logger.exception("charter-runner floor pass failed")
                    await events.emit_manage_charter_tick_error(reason=str(exc))
                finally:
                    if activity is not None:
                        activity(False)
        except asyncio.CancelledError:
            if activity is not None:
                activity(False)
            raise
