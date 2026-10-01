"""Async deferred-restart supervisor for git-integration-worker (P2 core).

Owns the deferred-drain lifecycle for ONE restart intent:

  1. begin-drain (worker flips the drain epoch -> rejects new mutating work ->
     CONVERGES to idle), persist the returned epoch + worker generation. An
     HTTP timeout here with a live pid retries (``drain_begin_stall``); the
     kill-without-epoch path needs the health/pid probe to report the process
     absent, or recycle mode to exhaust ``idle_escalate_s``;
  2. await ``git_worker.drain.completed`` for THIS intent's epoch + worker_id,
     event-driven and resume-aware via the event-service ``/v1/subscribe`` WS,
     with a periodic ``drain-state`` reconcile fallback when push is unavailable;
  3. a final one-shot stale-event epoch-check (R-C) — same worker generation,
     same epoch, draining, active_count==0 — before any kill;
  4. SIGTERM via the injected kill callable (``stop_git_integration_worker``).

A passed deadline while the drain-state probe still shows a live
process with claimed occupants is alert-only: ``manage.restart.timeout``
pages once and the supervisor keep-awaits. Operator bind 2026-07-24
(todo:manage-busy-drain-restart): do not auto-SIGKILL in-flight work
(closeout_relay hazard). Occupancy stall of a reachable process never
SIGTERMs. A target is dead only when the drain snapshot has been absent
for one heartbeat TTL and the health/pid probe does not see a process.
Restart-class actions then start it (the ``start`` callable) and do not
SIGTERM first. Stop actions complete without starting. The sole occupant
force on a reachable process is recycle ``idle_escalate_s``. Default
deadline is 7 days.

Steer-restart v1 (``todo:cursor-sdk-steer-restart``): live cursor-sdk dispatches
are not a reason to wait or kill. Step 1b — when the intent carries
``park_live`` (the default for GIW stop/restart/sync_restart) — waits
``PARK_LIVE_GRACE_S`` then asks GIW to *park* them
(``POST /api/v1/cursor/park-for-restart``: bridge ``CancelRun``, row terminal
``cancelled`` + park columns, GIW auto-resume after restart). The sweep parks
only resume-eligible occupants (``sdk_agent_id`` on the ledger row and the SDK
store dir on disk — ``preflight_park``); everyone else stays live and this loop
drain-waits. Recycle mode does not set ``park_live``: occupant idle tries the
same sweep before ``_sigterm`` and kills only when park is refused for a reason
a restart cannot clear.

All worker/event transports are injected callables so the lifecycle is unit
testable with a fake worker + fake event feed + fake kill (AC-2..AC-5). The
``build_git_worker_drain_supervisor`` factory wires the real HTTP + WS transports.
The restart-mutex slot is owned by ``restart_drain.run_gated_drain_supervised`` /
``resume_drain_supervision`` (released in their ``finally``); ``supervise`` is pure
lifecycle logic and never touches the gate.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import git_worker_liveness as _liveness
from .drain_begin_stall import begin_drain_or_wait
from .drain_dead_recovery import (
    START_ON_DEAD_ACTIONS,
    force_start_and_validate,
    lifecycle_result_certifies,
)
from .drain_timeout_keep_await import timeout_affordances
from .restart_intent_store import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    Intent,
    RestartIntentStore,
)

logger = get_logger(__name__)

_SERVICE = "git_integration_worker"
_DRAIN_COMPLETED_SIGNAL = "git_worker.drain.completed"
_DRAIN_SIGNAL_FILTER = "git_worker.drain.*"
_SUBSCRIBE_URL = "http://localhost/v1/subscribe"

# Operator bind 2026-07-24 (todo:manage-busy-drain-restart): a live pid past
# the ceiling is alert-only (never auto-SIGKILL in-flight work). A dead probe
# at that ceiling force-starts. Bar is days, not minutes, for the live case.
_DEFAULT_DEADLINE_S = 604800.0  # 7 days
_DEFAULT_RECONCILE_INTERVAL_S = 2.0
_DEFAULT_PROGRESS_INTERVAL_S = 30.0
# Consecutive reconcile polls confirming a different worker generation.
_GENERATION_GONE_CONFIRM_WINDOW_S = 6.0

# Injected transport callable types.
BeginDrainCaller = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
DrainStateCaller = Callable[[], Awaitable[dict[str, Any]]]
SubscribeFactory = Callable[[int], AsyncIterator[dict[str, Any]]]
KillCaller = Callable[[], Awaitable[str]]
# (intent_id, drain_epoch) -> drain_state snapshot after release attempt.
CancelDrainCaller = Callable[[str, int], Awaitable[dict[str, Any]]]
# (intent_id, drain_epoch, reason) -> park sweep summary
# {requested, refused:[{dispatch_id, refusal}], already_parked, live_after}.
ParkForRestartCaller = Callable[[str, int | None, str], Awaitable[dict[str, Any]]]

_AWAIT_CONVERGED = "converged"
_AWAIT_TIMEOUT = "timeout"
_AWAIT_CANCELLED = "cancelled"
_AWAIT_IDLE = "idle"
_AWAIT_GENERATION_GONE = "generation_gone"
_AWAIT_DEAD = "dead"
_AWAIT_HANDED_OFF = "handed_off"

# Park refusals a restart cannot clear by waiting: recycle falls through to kill.
_PARK_HARD_REFUSALS = frozenset({"CANCEL_FAILED", "NEST_CHAIN", "STATE_ROOT_MISSING"})
# Recycle park-first tries the sweep at most this many idle windows before force.
_PARK_IDLE_MAX_ATTEMPTS = 3
# Wait this long after begin-drain before step 1b. A heartbeating occupant is
# never idle, so park-on-_AWAIT_IDLE never fires for a restart. 20s lets a
# short call finish; still-busy resume-eligible occupants are then parked.
# Falsifier of this gate versus unconditional park: CancelRun on an occupant
# with no sdk_agent_id, or whose SDK store dir is absent, cannot resume.
PARK_LIVE_GRACE_S = 20.0


def _field(ev: dict[str, Any], key: str) -> Any:
    """Read a field that may be top-level or nested under ``payload``.

    Live WS pushes and event-store replays carry the signal name at top level
    (the subscribe filter keys on it) but the drain identity (drain_epoch,
    worker_id, ...) under ``payload`` (see topology._subscribe_for_connection).
    Read defensively so we are correct regardless of store flattening.
    """
    if key in ev:
        return ev[key]
    payload = ev.get("payload")
    if isinstance(payload, dict):
        return payload.get(key)
    return None


async def _aclose(agen: AsyncIterator[dict[str, Any]]) -> None:
    aclose = getattr(agen, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:  # pragma: no cover — best-effort cleanup
            logger.debug("drain subscription aclose failed", exc_info=True)


@dataclass(slots=True)
class GitWorkerDrainSupervisor:
    """Lifecycle owner for one git-worker deferred-restart intent."""

    store: RestartIntentStore
    begin_drain: BeginDrainCaller
    drain_state: DrainStateCaller
    subscribe_events: SubscribeFactory
    kill: KillCaller
    start: KillCaller | None = None
    cancel_drain: CancelDrainCaller | None = None
    deadline_s: float = _DEFAULT_DEADLINE_S
    reconcile_interval_s: float = _DEFAULT_RECONCILE_INTERVAL_S
    progress_interval_s: float = _DEFAULT_PROGRESS_INTERVAL_S
    idle_escalate_s: float | None = None
    liveness_state: DrainStateCaller | None = None
    park_for_restart: ParkForRestartCaller | None = None
    process_absent: Callable[[], Awaitable[bool]] | None = None
    read_pid: Callable[[], Awaitable[int | None]] | None = None
    park_live_grace_s: float = PARK_LIVE_GRACE_S
    _settle_boundary_monotonic: float | None = None
    _idle_last_progress: float | None = None
    _idle_token: tuple[frozenset[str], tuple[tuple[str, str], ...], bool] | None = None
    _park_idle_attempts: int = 0
    _last_park_summary: dict[str, Any] | None = None
    _last_probe_snapshot: dict[str, Any] | None = None

    async def supervise(self, intent: Intent) -> None:
        """Drive one intent from begin-drain to a terminal lifecycle.

        When ``start`` is set, a deadline ceiling or a dead probe force-starts
        and returns — the row must not stay ``pending_drain`` with no process.
        Otherwise a deadline emits ``manage.restart.timeout`` and keep-awaits.
        Cancel is observed until ``_final_epoch_check`` returns ok; after that
        the store advances to ``drained_restarting`` (kill committed) so manage
        cancel refuses.
        """
        self._settle_boundary_monotonic = None
        self._idle_last_progress = None
        self._idle_token = None
        self._park_idle_attempts = 0
        self._last_park_summary = None
        self._last_probe_snapshot = None
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        timeout_alerted = False
        try:
            begun = await begin_drain_or_wait(self, intent, t0=t0)
            if await self._abort_if_requested(intent) or begun is None:
                return
            intent = begun
            if intent.park_live:
                await self._park_live_after_grace(intent)
            while True:
                outcome = await self._await_drain_completed(intent, deadline, t0)
                if outcome == _AWAIT_CANCELLED:
                    if await self._abort_if_requested(intent):
                        return
                    await self._on_cancelled(intent)
                    return
                if outcome == _AWAIT_DEAD:
                    if self._may_start(intent):
                        await self._force_start(intent, reason="dead_target")
                    else:
                        await self._complete_stopped_target(intent)
                    return
                if outcome == _AWAIT_HANDED_OFF:
                    return
                if outcome == _AWAIT_TIMEOUT:
                    if await self._ceiling_should_force_start():
                        if self._may_start(intent):
                            await self._force_start(intent, reason="deadline_ceiling")
                        else:
                            await self._complete_stopped_target(intent)
                        return
                    # A live pid past the ceiling still pages. Starting would
                    # not replace in-flight work; the 2026-07-24 bind forbids
                    # that auto-kill. A dead probe force-starts above.
                    if not timeout_alerted:
                        await self._on_timeout(intent)
                        timeout_alerted = True
                    deadline = time.monotonic() + _DEFAULT_DEADLINE_S
                    continue
                if outcome == _AWAIT_IDLE:
                    if await self._park_first_on_idle(intent):
                        continue
                    await self._on_idle(intent, t0)
                    return
                if outcome == _AWAIT_GENERATION_GONE:
                    snapshot = await self._safe_drain_state()
                    await self._resolve_non_kill(intent, snapshot)
                    return
                break
            if await self._abort_if_requested(intent):
                return
            ok, snapshot = await self._final_epoch_check(intent)
            if await self._abort_if_requested(intent):
                return
            if not ok:
                await self._resolve_non_kill(intent, snapshot)
                return
            if not self._claim_kill(intent):
                await self._resolve_non_kill(intent, snapshot)
                return
            await events.emit_manage_restart_drain_completed(
                intent_id=intent.intent_id,
                drain_epoch=intent.drain_epoch or 0,
                worker_id=intent.worker_id,
            )
            await self._sigterm(intent, t0)
            if self.idle_escalate_s is not None:
                await events.emit_manage_recycle_completed(
                    intent_id=intent.intent_id,
                    escalated=False,
                    duration_s=time.monotonic() - t0,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — supervisor must not crash the loop
            logger.exception("drain supervisor failed: intent_id=%s", intent.intent_id)
            current = self.store.get(intent.intent_id)
            if current is not None and current.status in {
                STATUS_CANCELLED,
                STATUS_FORCE_REQUESTED,
            }:
                return
            # begin-drain never landed (drain_epoch stays null) AND
            # begin_drain_or_wait gave up: the health/pid probe reported the
            # process absent, or recycle mode exhausted idle_escalate_s on a
            # pid-alive, HTTP-wedged GIW (a:36019). Cooperative drain cannot
            # start — SIGTERM. A pid-alive stall alone retries there and never
            # reaches this kill (a:36911: one 10s timeout reaped six live
            # conductors).
            if intent.drain_epoch is None:
                await self._on_idle(intent, t0)
                return
            self.store.advance(intent.intent_id, status=STATUS_FAILED)
            await events.emit_manage_restart_failed(
                intent_id=intent.intent_id, reason=str(exc)
            )

    # --------------------------------------------------------------- step 1
    async def _begin_drain(self, intent: Intent) -> Intent:
        """Flip the worker drain epoch; persist the returned generation identity.

        Fresh intent (no stored epoch): read drain-state to derive next epoch.
        Reconcile (epoch already stored): re-drive with the SAME epoch — the
        worker's begin-drain is idempotent on (intent_id, drain_epoch).
        """
        if intent.drain_epoch is None:
            current = await self.drain_state()
            target_epoch = int(current.get("drain_epoch", 0) or 0) + 1
        else:
            target_epoch = intent.drain_epoch
        snapshot = await self.begin_drain(
            {
                "reason": intent.reason or "manage deferred restart",
                "intent_id": intent.intent_id,
                "drain_epoch": target_epoch,
                "deadline_s": self.deadline_s,
            }
        )
        epoch = int(snapshot.get("drain_epoch", target_epoch))
        worker_id = snapshot.get("worker_id")
        worker_started_at = snapshot.get("worker_started_at")
        self.store.set_drain_epoch(
            intent.intent_id,
            drain_epoch=epoch,
            worker_id=worker_id,
            worker_started_at=worker_started_at,
        )
        intent.drain_epoch = epoch
        intent.worker_id = worker_id
        intent.worker_started_at = worker_started_at
        started_mono = snapshot.get("drain_started_monotonic")
        if isinstance(started_mono, int | float):
            self._settle_boundary_monotonic = float(started_mono)
        elif self._settle_boundary_monotonic is None:
            self._settle_boundary_monotonic = time.monotonic()
        await events.emit_manage_restart_deferred(
            intent_id=intent.intent_id,
            service=intent.service,
            drain_epoch=epoch,
            deadline_at=intent.deadline_at or "",
        )
        return intent

    # -------------------------------------------------------------- step 1b
    async def _run_park_sweep(
        self, intent: Intent, *, reason: str
    ) -> dict[str, Any] | None:
        """Ask GIW to park live dispatches; persist the summary. None on failure."""
        if self.park_for_restart is None:
            return None
        try:
            summary = await self.park_for_restart(
                intent.intent_id, intent.drain_epoch, reason
            )
        except Exception:  # noqa: BLE001 — park is best-effort; keep-await remains
            logger.exception(
                "park-for-restart sweep failed: intent_id=%s", intent.intent_id
            )
            return None
        if not isinstance(summary, dict):
            return None
        self._last_park_summary = summary
        try:
            self.store.set_park_summary(intent.intent_id, summary=summary)
        except Exception:  # noqa: BLE001 — projection only
            logger.debug("park summary persist failed", exc_info=True)
        return summary

    async def _park_live_after_grace(self, intent: Intent) -> None:
        """Wait ``park_live_grace_s``, then park if the drain is still busy.

        ``park_live_grace_s <= 0`` parks immediately (tests of step 1b). A drain
        that converges during the grace skips the sweep. Cancel during the grace
        returns without parking; the caller observes the abort next.
        """
        if self.park_live_grace_s > 0:
            grace_end = time.monotonic() + self.park_live_grace_s
            while time.monotonic() < grace_end:
                if self._abort_kind(intent) is not None:
                    return
                snapshot = await self._safe_drain_state()
                if snapshot is not None and self._drain_state_matches(snapshot, intent):
                    return
                remaining = grace_end - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self.reconcile_interval_s, remaining))
            if self._abort_kind(intent) is not None:
                return
            snapshot = await self._safe_drain_state()
            if snapshot is not None and self._drain_state_matches(snapshot, intent):
                return
        await self._park_live(intent)

    async def _park_live(self, intent: Intent) -> None:
        """Step 1b: park live cursor-sdk dispatches so drain converges without kill.

        GIW ``preflight_park`` parks an occupant only when ``sdk_agent_id`` is on
        the ledger row and ``resolve_sdk_store_dir`` finds the store on disk.
        ``NOT_RESUMABLE_YET`` and ``STATE_ROOT_MISSING`` are not requested; those
        occupants stay in the drain set and this loop keep-awaits them. Other
        refusals do not fail the intent either.
        """
        summary = await self._run_park_sweep(
            intent, reason=intent.reason or "manage restart (park_live)"
        )
        if summary is None:
            return
        await events.emit_manage_restart_park_live_requested(
            intent_id=intent.intent_id,
            requested=list(summary.get("requested") or []),
            refused=list(summary.get("refused") or []),
            live_after=int(summary.get("live_after") or 0),
        )
        logger.info(
            "park_live sweep: intent_id=%s requested=%s refused=%s live_after=%s",
            intent.intent_id,
            summary.get("requested"),
            summary.get("refused"),
            summary.get("live_after"),
        )

    @staticmethod
    def _hard_refusals(summary: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            r
            for r in (summary.get("refused") or [])
            if isinstance(r, dict) and str(r.get("refusal")) in _PARK_HARD_REFUSALS
        ]

    async def _park_first_on_idle(self, intent: Intent) -> bool:
        """Recycle park-first rung. True ⇒ keep awaiting; False ⇒ escalate to kill.

        A successful sweep (nothing left that a park could not free) resets the
        idle clock so the parked rows get a window to close their tickets. Hard
        refusals (``CANCEL_FAILED`` / ``NEST_CHAIN`` / ``STATE_ROOT_MISSING``) or
        the attempt cap fall through to the existing force kill.
        """
        if self.park_for_restart is None:
            return False
        if self._park_idle_attempts >= _PARK_IDLE_MAX_ATTEMPTS:
            return False
        self._park_idle_attempts += 1
        summary = await self._run_park_sweep(
            intent, reason=intent.reason or "manage recycle (park-first)"
        )
        if summary is None or self._hard_refusals(summary):
            return False
        self._idle_last_progress = time.monotonic()
        self._idle_token = None
        logger.info(
            "recycle park-first: intent_id=%s attempt=%s requested=%s live_after=%s",
            intent.intent_id,
            self._park_idle_attempts,
            summary.get("requested"),
            summary.get("live_after"),
        )
        return True

    # --------------------------------------------------------------- step 2
    async def _await_drain_completed(
        self, intent: Intent, deadline: float, start: float
    ) -> str:
        """Await drain convergence, idle escalate, deadline timeout, or cancel.

        Returns ``converged`` | ``idle`` | ``timeout`` | ``cancelled`` |
        ``dead`` | ``handed_off``. Unified loop: matching ``drain.completed``
        plus drain-state reconcile plus optional idle-on-no-progress (recycle
        mode). No snapshot for the confirm window, and a health/pid probe that
        does not see a process, returns ``dead`` before the deadline is
        consulted. A snapshot that arrives is not dead, whatever its pid
        field contains. A live occupant past the ceiling is the alert-only
        timeout.
        """
        last_progress = start
        probe_fail_streak = 0
        probe_unreachable_alerted = False
        generation_gone_streak = 0
        probe_unreachable_threshold = _polls_for_window(
            _liveness.unreachable_window_s(), self.reconcile_interval_s
        )
        generation_gone_threshold = _polls_for_window(
            _GENERATION_GONE_CONFIRM_WINDOW_S, self.reconcile_interval_s
        )
        try:
            agen: AsyncIterator[dict[str, Any]] | None = self.subscribe_events(
                intent.last_seen_event_seq
            )
        except Exception:  # noqa: BLE001 — subscription optional; fall back to pull
            logger.warning("drain subscription unavailable; using reconcile poll")
            agen = None
        try:
            while True:
                if self._abort_kind(intent) is not None:
                    return _AWAIT_CANCELLED
                now = time.monotonic()
                if now - last_progress >= self.progress_interval_s:
                    await self._emit_progress(intent, now - start)
                    # The 30s tick is not a re-arm. An abandoned intent whose
                    # only signal is this tick expires through cancel.
                    try:
                        expired = await self._expire_abandoned(intent)
                    except Exception:
                        logger.warning(
                            "restart intent expiry tick failed; row stays "
                            "pending_drain intent_id=%s",
                            intent.intent_id,
                            exc_info=True,
                        )
                        expired = False
                    if expired:
                        return _AWAIT_CANCELLED
                    last_progress = now
                snapshot = await self._safe_drain_state()
                if snapshot is not None:
                    self._last_probe_snapshot = snapshot
                    probe_fail_streak = 0
                    probe_unreachable_alerted = False
                    if self._generation_gone(snapshot, intent):
                        generation_gone_streak += 1
                        if generation_gone_streak >= generation_gone_threshold:
                            if await self._reconcile_observed_start(intent, snapshot):
                                return _AWAIT_HANDED_OFF
                            return _AWAIT_GENERATION_GONE
                    else:
                        generation_gone_streak = 0
                    if self._drain_state_matches(snapshot, intent):
                        return _AWAIT_CONVERGED
                    if await self._idle_gate_tripped(snapshot, now, start):
                        return _AWAIT_IDLE
                else:
                    generation_gone_streak = 0
                    probe_fail_streak += 1
                    if (
                        not probe_unreachable_alerted
                        and probe_fail_streak >= probe_unreachable_threshold
                    ):
                        await self._emit_probe_unreachable(
                            intent,
                            elapsed_s=now - start,
                            consecutive_failures=probe_fail_streak,
                        )
                        probe_unreachable_alerted = True
                    if probe_fail_streak >= probe_unreachable_threshold:
                        absent = await self._health_pid_absent()
                        if _liveness.drain_target_is_dead(
                            consecutive_snapshot_misses=probe_fail_streak,
                            miss_threshold=probe_unreachable_threshold,
                            health_pid_absent=absent,
                        ):
                            return _AWAIT_DEAD
                # The dead predicate already returned above. A live occupant
                # past the ceiling is alert-only and does not start.
                if now >= deadline:
                    return _AWAIT_TIMEOUT
                if agen is None:
                    await asyncio.sleep(self.reconcile_interval_s)
                    continue
                try:
                    ev = await asyncio.wait_for(
                        agen.__anext__(), timeout=self.reconcile_interval_s
                    )
                except StopAsyncIteration:
                    agen = None
                    continue
                except TimeoutError:
                    continue
                except Exception:  # noqa: BLE001 — push failed; fall to reconcile
                    logger.warning("drain subscription errored; reconcile only")
                    await _aclose(agen)
                    agen = None
                    continue
                seq = _field(ev, "seq")
                if isinstance(seq, int):
                    self.store.set_last_seen_seq(intent.intent_id, seq)
                if self._event_matches(ev, intent):
                    return _AWAIT_CONVERGED
        finally:
            if agen is not None:
                await _aclose(agen)

    # --------------------------------------------------------------- step 3
    async def _final_epoch_check(
        self, intent: Intent
    ) -> tuple[bool, dict[str, Any] | None]:
        """One non-looping drain-state read; True only if safe to SIGTERM."""
        snapshot = await self._safe_drain_state()
        if snapshot is None:
            return False, None
        return self._drain_state_matches(snapshot, intent), snapshot

    # --------------------------------------------------------------- step 4
    async def _sigterm(self, intent: Intent, t0: float) -> None:
        """Deliver SIGTERM after kill-commit (``drained_restarting`` already set)."""
        from .git_worker_activation_verify import record_kill_boundary_and_arm_verify

        current = self.store.get(intent.intent_id)
        if current is None or current.status in {
            STATUS_CANCELLED,
            STATUS_FORCE_REQUESTED,
        }:
            return
        if current.status != STATUS_DRAINED_RESTARTING:
            self.store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING)
        try:
            message = await self.kill()
        except Exception as exc:  # noqa: BLE001
            logger.exception("drain SIGTERM failed: intent_id=%s", intent.intent_id)
            self.store.advance(intent.intent_id, status=STATUS_FAILED)
            await events.emit_manage_restart_failed(
                intent_id=intent.intent_id, reason=f"kill failed: {exc}"
            )
            return
        if not lifecycle_result_certifies(message, action=current.action):
            logger.warning(
                "drain lifecycle did not confirm: intent_id=%s -> %s",
                intent.intent_id,
                message[:200],
            )
            self.store.advance(intent.intent_id, status=STATUS_FAILED)
            await events.emit_manage_restart_failed(
                intent_id=intent.intent_id,
                reason=f"lifecycle unconfirmed: {message[:200]}",
            )
            return
        logger.info(
            "deferred git-worker restart completed: intent_id=%s -> %s",
            intent.intent_id,
            message[:200],
        )
        await events.emit_manage_restart_completed(
            intent_id=intent.intent_id, duration_s=time.monotonic() - t0
        )
        await record_kill_boundary_and_arm_verify(
            self.store,
            intent,
            boundary_source="kill_return",
        )

    # --------------------------------------------------------------- step 5
    async def _on_cancelled(self, intent: Intent) -> None:
        """Abort without kill after store cancel; belt-and-suspenders drain-release."""
        if intent.drain_epoch is not None and self.cancel_drain is not None:
            try:
                await self.cancel_drain(intent.intent_id, intent.drain_epoch)
            except Exception:  # noqa: BLE001 — manage owns primary release; log only
                logger.exception(
                    "supervisor cancel-drain release failed: intent_id=%s",
                    intent.intent_id,
                )
        logger.info(
            "deferred git-worker restart cancelled (no SIGTERM): intent_id=%s",
            intent.intent_id,
        )
        await events.emit_manage_restart_cancelled(intent_id=intent.intent_id)

    async def _on_idle(self, intent: Intent, t0: float) -> None:
        """Force-kill when recycle occupants idle, or begin-drain never lands."""
        snapshot = await self._safe_drain_state() or {}
        idle_s = float(self.idle_escalate_s or 0.0)
        park_summary = self._last_park_summary
        if self.idle_escalate_s is not None:
            await events.emit_manage_recycle_escalated(
                intent_id=intent.intent_id,
                idle_s=idle_s,
                active_count=int(snapshot.get("active_count", 0) or 0),
                stuck_ops=self._stuck_ops(snapshot),
                park_attempted=self._park_idle_attempts > 0,
                park_refusals=(
                    list(park_summary.get("refused") or []) if park_summary else []
                ),
            )
            logger.warning(
                "recycle_giw idle-escalate to force kill: intent_id=%s active_count=%s",
                intent.intent_id,
                snapshot.get("active_count"),
            )
        else:
            logger.warning(
                "git-worker begin-drain unreachable and process absent; "
                "SIGTERM without epoch: intent_id=%s",
                intent.intent_id,
            )
        self.store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING)
        await self._sigterm(intent, t0)
        if self.idle_escalate_s is not None:
            await events.emit_manage_recycle_completed(
                intent_id=intent.intent_id,
                escalated=True,
                duration_s=time.monotonic() - t0,
            )

    async def _idle_gate_tripped(
        self, snapshot: dict[str, Any], now: float, start: float
    ) -> bool:
        """True when recycle mode sees no occupant progress for idle_escalate_s."""
        if self.idle_escalate_s is None:
            return False
        if int(snapshot.get("active_count", 0) or 0) <= 0:
            return False
        liveness = None
        if self.liveness_state is not None:
            try:
                liveness = await self.liveness_state()
            except Exception:  # noqa: BLE001 — probe optional; drain-state still binds
                logger.debug("recycle liveness probe failed", exc_info=True)
        from .giw_recycle import occupant_progress_fresh

        fresh, token = occupant_progress_fresh(
            snapshot,
            liveness,
            idle_s=self.idle_escalate_s,
            previous_token=self._idle_token,
        )
        self._idle_token = token
        if self._idle_last_progress is None:
            self._idle_last_progress = start
        if fresh:
            self._idle_last_progress = now
            return False
        return (now - self._idle_last_progress) >= self.idle_escalate_s

    async def _on_timeout(self, intent: Intent) -> None:
        """Page once. Do not terminalize — a later idle must still SIGTERM."""
        snapshot = await self._safe_drain_state() or {}
        await events.emit_manage_restart_timeout(
            intent_id=intent.intent_id,
            service=intent.service,
            deadline_at=intent.deadline_at,
            stuck_ops=self._stuck_ops(snapshot),
            affordances=timeout_affordances(intent.intent_id),
        )
        logger.warning(
            "deferred git-worker restart timed out (alert-only; keep-await continues): "
            "intent_id=%s",
            intent.intent_id,
        )

    async def _expire_abandoned(self, intent: Intent) -> bool:
        """Cancel an arm past its window. The 30s tick is the only caller."""
        from .restart_intent_expiry import expire_via_cancel

        return await expire_via_cancel(
            self.store,
            intent.intent_id,
            release_drain=self.cancel_drain,
        )

    def _abort_kind(self, intent: Intent) -> str | None:
        current = self.store.get(intent.intent_id)
        if current is None:
            return None
        if current.status == STATUS_FORCE_REQUESTED:
            return "force"
        if current.status == STATUS_CANCELLED:
            return "cancel"
        return None

    async def _abort_if_requested(self, intent: Intent) -> bool:
        """True when supervise should return without kill.

        Force-preempt leaves ``_draining`` set so Auto cannot ``claim_next``
        before the force path SIGTERMs. Cancel still ``release_drain``.
        """
        kind = self._abort_kind(intent)
        if kind == "force":
            logger.info(
                "deferred git-worker restart force-preempted (no drain-release): "
                "intent_id=%s",
                intent.intent_id,
            )
            return True
        if kind == "cancel":
            await self._on_cancelled(intent)
            return True
        return False

    async def _resolve_non_kill(
        self, intent: Intent, snapshot: dict[str, Any] | None
    ) -> None:
        """Final check failed: complete if the target generation is gone, else fail."""
        from .git_worker_activation_verify import arm_verify_after_generation_gone

        if snapshot is None or self._generation_gone(snapshot, intent):
            if await arm_verify_after_generation_gone(self.store, intent):
                return
            self.store.advance(intent.intent_id, status=STATUS_COMPLETED)
            await events.emit_manage_restart_completed(
                intent_id=intent.intent_id, duration_s=0.0
            )
            logger.info(
                "drain target worker generation already gone; intent completed "
                "without kill: intent_id=%s",
                intent.intent_id,
            )
            return
        self.store.advance(intent.intent_id, status=STATUS_FAILED)
        await events.emit_manage_restart_failed(
            intent_id=intent.intent_id,
            reason="final epoch-check mismatch (epoch/draining/active) on the same worker generation",
        )

    # ----------------------------------------------------------- predicates
    def _claim_kill(self, intent: Intent) -> bool:
        if (
            not intent.worker_id
            or not intent.worker_started_at
            or intent.drain_epoch is None
        ):
            return False
        return self.store.claim_kill(
            intent.intent_id,
            worker_id=intent.worker_id,
            worker_started_at=intent.worker_started_at,
            drain_epoch=intent.drain_epoch,
        )

    def _intent_cancelled(self, intent: Intent) -> bool:
        return self._abort_kind(intent) == "cancel"

    def _event_matches(self, ev: dict[str, Any], intent: Intent) -> bool:
        return (
            _field(ev, "signal") == _DRAIN_COMPLETED_SIGNAL
            and _field(ev, "drain_epoch") == intent.drain_epoch
            and _field(ev, "worker_id") == intent.worker_id
        )

    def _drain_state_matches(self, snap: dict[str, Any], intent: Intent) -> bool:
        """Shared identity + idle predicate (steps 2-fallback and 3)."""
        return (
            not self._generation_gone(snap, intent)
            and snap.get("drain_epoch") == intent.drain_epoch
            and bool(snap.get("draining"))
            and int(snap.get("active_count", -1)) == 0
        )

    @staticmethod
    def _generation_gone(snap: dict[str, Any], intent: Intent) -> bool:
        """True iff the snapshot is a DIFFERENT worker generation than the intent."""
        return (
            snap.get("worker_id") != intent.worker_id
            or snap.get("worker_started_at") != intent.worker_started_at
        )

    @staticmethod
    def _stuck_ops(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        drain_started = snapshot.get("drain_started_at")
        ops: list[dict[str, Any]] = []
        for op in snapshot.get("active_ops", []) or []:
            admitted_at = op.get("admitted_at")
            ops.append(
                {
                    "op_id": op.get("op_id"),
                    "kind": op.get("kind"),
                    "route": op.get("route"),
                    "admitted_at": admitted_at,
                    "admitted_during_drain": bool(
                        drain_started and admitted_at and admitted_at > drain_started
                    ),
                }
            )
        return ops

    # --------------------------------------------------------------- helpers

    async def _reconcile_observed_start(
        self, intent: Intent, snapshot: dict[str, Any]
    ) -> bool:
        """Validate a pending activation when a new pid serves the code_ref.

        True means the intent is completed and the caller must not start again.
        """
        from charter_runner_store.propagation_validation import (
            latest_validation_for_intent,
        )

        from .git_worker_activation_verify import reconcile_out_of_band_start
        from .restart_intent_store import STATUS_COMPLETED, STATUS_PENDING_DRAIN

        current = self.store.get(intent.intent_id)
        if current is None or current.status != STATUS_PENDING_DRAIN:
            return False
        pid = snapshot.get("pid")
        code_version = snapshot.get("code_version")
        if not isinstance(pid, int):
            return False
        if not isinstance(code_version, str) or not code_version.strip():
            return False
        try:
            validation = latest_validation_for_intent(intent.intent_id)
        except Exception:
            logger.debug("out-of-band validation lookup failed", exc_info=True)
            return False
        if validation is None or validation.outcome != "pending":
            return False
        outcome = reconcile_out_of_band_start(
            validation_id=validation.validation_id,
            observed_pid=pid,
            observed_code_version=code_version,
            prior_pid=None,
            pid_changed=True,
        )
        if outcome != "validated":
            return False
        self.store.advance_if_status(
            intent.intent_id,
            from_status=STATUS_PENDING_DRAIN,
            to_status=STATUS_COMPLETED,
            reason="out-of-band start satisfied code_ref",
        )
        return True

    def _may_start(self, intent: Intent) -> bool:
        """Restart-class actions with a start callable may replace a dead target."""
        return self.start is not None and intent.action in START_ON_DEAD_ACTIONS

    async def _complete_stopped_target(self, intent: Intent) -> None:
        """The target is gone and this action must not start it."""
        current = self.store.get(intent.intent_id)
        if current is None or current.status != STATUS_PENDING_DRAIN:
            return
        self.store.advance(intent.intent_id, status=STATUS_COMPLETED)
        logger.info(
            "drain target gone; completed without start: intent_id=%s action=%s",
            intent.intent_id,
            intent.action,
        )

    async def _health_pid_absent(self) -> bool:
        """True only when the injected probe says no process is present."""
        if self.process_absent is None:
            return False
        try:
            return bool(await self.process_absent())
        except Exception:  # noqa: BLE001 — unknown liveness is not "absent"
            logger.debug("health/pid probe failed", exc_info=True)
            return False

    async def _ceiling_should_force_start(self) -> bool:
        """True only after the same consecutive-miss window as the await loop.

        One unanswered probe is not dead. A snapshot that arrives, including
        one whose pid field is missing, is not dead. The health/pid probe
        must also report the process absent.
        """
        if self.start is None:
            return False
        threshold = _polls_for_window(
            _liveness.unreachable_window_s(), self.reconcile_interval_s
        )
        misses = 0
        while misses < threshold:
            snap = await self._safe_drain_state()
            if snap is not None:
                self._last_probe_snapshot = snap
                return False
            misses += 1
            if misses < threshold:
                await asyncio.sleep(self.reconcile_interval_s)
        return _liveness.drain_target_is_dead(
            consecutive_snapshot_misses=misses,
            miss_threshold=threshold,
            health_pid_absent=await self._health_pid_absent(),
        )

    async def _force_start(self, intent: Intent, *, reason: str) -> None:
        """Start the worker and validate. Never the kill/stop callable."""
        if self.start is None or not self._may_start(intent):
            return
        prior_pid: int | None = None
        snap = self._last_probe_snapshot
        if isinstance(snap, dict):
            pid = snap.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool):
                prior_pid = pid
        await force_start_and_validate(
            self.store,
            intent,
            self.start,
            reason=reason,
            prior_pid=prior_pid,
            read_pid=self.read_pid,
        )

    async def _safe_drain_state(self) -> dict[str, Any] | None:
        try:
            return await self.drain_state()
        except Exception:  # noqa: BLE001 — transient; reconcile retries next window
            logger.debug("drain-state probe failed", exc_info=True)
            return None

    async def _emit_probe_unreachable(
        self,
        intent: Intent,
        *,
        elapsed_s: float,
        consecutive_failures: int,
    ) -> None:
        stuck_source = self._last_probe_snapshot or {}
        await events.emit_manage_restart_probe_unreachable(
            intent_id=intent.intent_id,
            service=intent.service,
            action=intent.action,
            drain_epoch=intent.drain_epoch,
            worker_id=intent.worker_id,
            elapsed_s=elapsed_s,
            consecutive_failures=consecutive_failures,
            stuck_ops=self._stuck_ops(stuck_source),
        )
        logger.warning(
            "drain-state probe unreachable (alert-only; keep-await): "
            "intent_id=%s consecutive_failures=%s elapsed_s=%.1f",
            intent.intent_id,
            consecutive_failures,
            elapsed_s,
        )

    async def _emit_progress(self, intent: Intent, elapsed_s: float) -> None:
        snapshot = await self._safe_drain_state()
        probe_ok = snapshot is not None
        if probe_ok:
            self._last_probe_snapshot = snapshot
        snap = snapshot or {}
        await events.emit_manage_restart_draining(
            intent_id=intent.intent_id,
            service=intent.service,
            elapsed_s=elapsed_s,
            active_count=int(snap.get("active_count", 0) or 0),
            active_ops=snap.get("active_ops", []) or [],
            probe_ok=probe_ok,
        )


def _polls_for_window(window_s: float, interval_s: float) -> int:
    """Minimum consecutive reconcile polls to cover ``window_s``."""
    return _liveness.polls_for_window(window_s, interval_s)


def build_git_worker_drain_supervisor(
    store: RestartIntentStore,
    *,
    worker_url: str,
    events_query_socket: str,
    kill: KillCaller,
    start: KillCaller | None = None,
    deadline_s: float = _DEFAULT_DEADLINE_S,
    idle_escalate_s: float | None = None,
    park_first: bool = True,
    process_absent: Callable[[], Awaitable[bool]] | None = None,
    read_pid: Callable[[], Awaitable[int | None]] | None = None,
) -> GitWorkerDrainSupervisor:
    """Construct a supervisor wired to the live worker + event service.

    ``park_first`` wires the ``park-for-restart`` transport (step 1b for
    ``park_live`` intents; park-first rung in recycle mode). It is the default
    posture — pass ``False`` only to reproduce the pre-steer wait-or-kill shape.
    """
    from transport_utils import make_async_client

    async def _begin_drain(body: dict[str, Any]) -> dict[str, Any]:
        async with make_async_client(worker_url, timeout=10.0) as client:
            resp = await client.post("/api/v1/git/admin/begin-drain", json=body)
            resp.raise_for_status()
            return resp.json()

    async def _park_for_restart(
        intent_id: str, drain_epoch: int | None, reason: str
    ) -> dict[str, Any]:
        # The sweep issues one bridge CancelRun per live dispatch; allow more
        # than the 10s control-plane budget.
        async with make_async_client(worker_url, timeout=60.0) as client:
            resp = await client.post(
                "/api/v1/cursor/park-for-restart",
                json={
                    "intent_id": intent_id,
                    "drain_epoch": drain_epoch,
                    "actor": "manage",
                    "reason": reason,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def _drain_state() -> dict[str, Any]:
        async with make_async_client(worker_url, timeout=10.0) as client:
            resp = await client.get("/api/v1/git/admin/drain-state")
            resp.raise_for_status()
            return resp.json()

    async def _cancel_drain(intent_id: str, drain_epoch: int) -> dict[str, Any]:
        async with make_async_client(worker_url, timeout=10.0) as client:
            resp = await client.post(
                "/api/v1/git/admin/cancel-drain",
                json={"intent_id": intent_id, "drain_epoch": drain_epoch},
            )
            resp.raise_for_status()
            return resp.json()

    async def _subscribe(resume_seq: int) -> AsyncIterator[dict[str, Any]]:
        import aiohttp

        connector = aiohttp.UnixConnector(path=events_query_socket)
        async with (
            aiohttp.ClientSession(connector=connector) as session,
            session.ws_connect(_SUBSCRIBE_URL) as ws,
        ):
            await ws.send_json(
                {
                    "type": "subscribe",
                    "filter": {"signal": _DRAIN_SIGNAL_FILTER},
                    "resume_from": {"seq": resume_seq},
                }
            )
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict) and data.get("type") != "subscribed":
                        yield data
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    return GitWorkerDrainSupervisor(
        store=store,
        begin_drain=_begin_drain,
        drain_state=_drain_state,
        subscribe_events=_subscribe,
        kill=kill,
        start=start,
        cancel_drain=_cancel_drain,
        deadline_s=deadline_s,
        idle_escalate_s=idle_escalate_s,
        liveness_state=None,
        park_for_restart=_park_for_restart if park_first else None,
        process_absent=process_absent,
        read_pid=read_pid,
    )


__all__ = [
    "PARK_LIVE_GRACE_S",
    "GitWorkerDrainSupervisor",
    "build_git_worker_drain_supervisor",
]
