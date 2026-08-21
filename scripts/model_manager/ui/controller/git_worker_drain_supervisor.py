"""Async deferred-restart supervisor for git-integration-worker (P2 core).

Owns the deferred-drain lifecycle for ONE restart intent:

  1. begin-drain (worker flips the drain epoch -> rejects new mutating work ->
     CONVERGES to idle), persist the returned epoch + worker generation;
  2. await ``git_worker.drain.completed`` for THIS intent's epoch + worker_id,
     event-driven and resume-aware via the event-service ``/v1/subscribe`` WS,
     with a periodic ``drain-state`` reconcile fallback when push is unavailable;
  3. a final one-shot stale-event epoch-check (R-C) — same worker generation,
     same epoch, draining, active_count==0 — before any kill;
  4. SIGTERM via the injected kill callable (``stop_git_integration_worker``).

Timeout-as-alert (R-F): if the deadline passes before convergence the intent goes
to ``timeout`` and ``manage.restart.timeout`` is emitted with the stuck-op
identity + the explicit-force affordance. The supervisor NEVER auto-SIGKILLs.
Default deadline is 7 days (assume drain inevitable under normal holds —
todo:manage-busy-drain-restart); progress heartbeats remain the visibility path.

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
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

from scripts.model_manager import observation_event as events
from services.git_integration_worker.drain_progress import (
    STALL_WINDOW_S,
    OccupancyProgressTracker,
)

from .restart_intent_store import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_TIMEOUT,
    Intent,
    RestartIntentStore,
)

logger = get_logger(__name__)

_SERVICE = "git_integration_worker"
_DRAIN_COMPLETED_SIGNAL = "git_worker.drain.completed"
_DRAIN_SIGNAL_FILTER = "git_worker.drain.*"
_SUBSCRIBE_URL = "http://localhost/v1/subscribe"

# Operator bind 2026-07-24 (todo:manage-busy-drain-restart): assume drain is
# inevitable under normal holds (incl. long cursor-sdk windows). Timeout stays
# alert-only (never auto-SIGKILL); bar is days, not minutes.
_DEFAULT_DEADLINE_S = 604800.0  # 7 days
_DEFAULT_RECONCILE_INTERVAL_S = 2.0
_DEFAULT_PROGRESS_INTERVAL_S = 30.0

# Injected transport callable types.
BeginDrainCaller = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
DrainStateCaller = Callable[[], Awaitable[dict[str, Any]]]
SubscribeFactory = Callable[[int], AsyncIterator[dict[str, Any]]]
KillCaller = Callable[[], Awaitable[str]]
# (intent_id, drain_epoch) -> drain_state snapshot after release attempt.
CancelDrainCaller = Callable[[str, int], Awaitable[dict[str, Any]]]

_AWAIT_CONVERGED = "converged"
_AWAIT_TIMEOUT = "timeout"
_AWAIT_CANCELLED = "cancelled"
_AWAIT_STALLED = "stalled"
_AWAIT_IDLE = "idle"


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
    cancel_drain: CancelDrainCaller | None = None
    deadline_s: float = _DEFAULT_DEADLINE_S
    reconcile_interval_s: float = _DEFAULT_RECONCILE_INTERVAL_S
    progress_interval_s: float = _DEFAULT_PROGRESS_INTERVAL_S
    stall_window_s: float = STALL_WINDOW_S
    on_timeout_mutex_release: Callable[[], Awaitable[None]] | None = None
    idle_escalate_s: float | None = None
    liveness_state: DrainStateCaller | None = None
    _settle_boundary_monotonic: float | None = None
    _progress_tracker: OccupancyProgressTracker | None = field(default=None, repr=False)
    _idle_last_progress: float | None = None
    _idle_token: tuple[frozenset[str], tuple[tuple[str, str], ...], bool] | None = None

    async def supervise(self, intent: Intent) -> None:
        """Drive one intent from begin-drain to SIGTERM (or alert-only timeout).

        Cancel is observed until ``_final_epoch_check`` returns ok; after that the
        store advances to ``drained_restarting`` (kill committed) so manage cancel
        refuses. There is no cancel poll between drain.completed emit and kill —
        the refuse boundary is the final-check ok commit, not a vague "during drain".
        """
        self._settle_boundary_monotonic = None
        self._idle_last_progress = None
        self._idle_token = None
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        self._progress_tracker = OccupancyProgressTracker(
            stall_window_s=self.stall_window_s
        )
        self._progress_tracker.reset(t0)
        timeout_alerted = False
        try:
            intent = await self._begin_drain(intent)
            if self._intent_cancelled(intent):
                await self._on_cancelled(intent)
                return
            while True:
                outcome = await self._await_drain_completed(intent, deadline, t0)
                if outcome == _AWAIT_CANCELLED:
                    await self._on_cancelled(intent)
                    return
                if outcome == _AWAIT_TIMEOUT:
                    if not timeout_alerted:
                        await self._on_timeout(intent)
                        timeout_alerted = True
                    deadline = time.monotonic() + _DEFAULT_DEADLINE_S
                    continue
                if outcome == _AWAIT_STALLED:
                    await self._force_kill_from_stall(intent, t0)
                    return
                if outcome == _AWAIT_IDLE:
                    await self._on_idle(intent, t0)
                    return
                break
            if self._intent_cancelled(intent):
                await self._on_cancelled(intent)
                return
            ok, snapshot = await self._final_epoch_check(intent)
            if self._intent_cancelled(intent):
                await self._on_cancelled(intent)
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
            if current is not None and current.status == STATUS_CANCELLED:
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

    # --------------------------------------------------------------- step 2
    async def _await_drain_completed(
        self, intent: Intent, deadline: float, start: float
    ) -> str:
        """Await drain convergence, idle escalate, deadline timeout, or cancel.

        Returns ``converged`` | ``idle`` | ``timeout`` | ``cancelled``. Unified
        loop: matching ``drain.completed`` plus drain-state reconcile plus
        optional idle-on-no-progress (recycle mode). Timeout stays alert-only.
        """
        last_progress = start
        try:
            agen: AsyncIterator[dict[str, Any]] | None = self.subscribe_events(
                intent.last_seen_event_seq
            )
        except Exception:  # noqa: BLE001 — subscription optional; fall back to pull
            logger.warning("drain subscription unavailable; using reconcile poll")
            agen = None
        try:
            while True:
                if self._intent_cancelled(intent):
                    return _AWAIT_CANCELLED
                now = time.monotonic()
                if now >= deadline:
                    return _AWAIT_TIMEOUT
                if now - last_progress >= self.progress_interval_s:
                    await self._emit_progress(intent, now - start)
                    last_progress = now
                snapshot = await self._safe_drain_state()
                if snapshot is not None and self._snapshot_stalled(snapshot, now):
                    return _AWAIT_STALLED
                if snapshot is not None and self._drain_state_matches(snapshot, intent):
                    return _AWAIT_CONVERGED
                if snapshot is not None and await self._idle_gate_tripped(snapshot, now, start):
                    return _AWAIT_IDLE
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
        if current is None or current.status == STATUS_CANCELLED:
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
        """Occupant progress idled; force-kill without waiting for active_count==0."""
        snapshot = await self._safe_drain_state() or {}
        idle_s = float(self.idle_escalate_s or 0.0)
        await events.emit_manage_recycle_escalated(
            intent_id=intent.intent_id,
            idle_s=idle_s,
            active_count=int(snapshot.get("active_count", 0) or 0),
            stuck_ops=self._stuck_ops(snapshot),
        )
        logger.warning(
            "recycle_giw idle-escalate to force kill: intent_id=%s active_count=%s",
            intent.intent_id,
            snapshot.get("active_count"),
        )
        self.store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING)
        await self._sigterm(intent, t0)
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
        snapshot = await self._safe_drain_state() or {}
        self.store.advance(intent.intent_id, status=STATUS_TIMEOUT)
        await events.emit_manage_restart_timeout(
            intent_id=intent.intent_id,
            service=intent.service,
            deadline_at=intent.deadline_at,
            stuck_ops=self._stuck_ops(snapshot),
            affordances=[
                "inspect: manage(action='busy_status')",
                (
                    "cancel: manage(action='cancel_restart_intent', "
                    f"intent_id='{intent.intent_id}')"
                ),
                "explicit force: manage(action='restart', service='git_integration_worker', force=true)",
            ],
        )
        logger.warning(
            "deferred git-worker restart timed out (alert-only; keep-await continues): "
            "intent_id=%s",
            intent.intent_id,
        )
        if self.on_timeout_mutex_release is not None:
            await self.on_timeout_mutex_release()

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
    def _snapshot_stalled(self, snap: dict[str, Any], now_mono: float) -> bool:
        """Prefer GIW ``stalled``; fall back to a local tracker for old snapshots."""
        if "stalled" in snap:
            return bool(snap["stalled"])
        tracker = self._progress_tracker
        if tracker is None:
            return False
        ops = snap.get("active_ops") or []
        return tracker.stalled(ops, now_mono=now_mono)

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

    async def _force_kill_from_stall(self, intent: Intent, t0: float) -> None:
        """R1′ occupied-stall or R2′ completed-unconsumed: force SIGTERM via CAS."""
        snapshot = await self._safe_drain_state()
        if snapshot is not None and self._generation_gone(snapshot, intent):
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

    def _intent_cancelled(self, intent: Intent) -> bool:
        current = self.store.get(intent.intent_id)
        return current is not None and current.status == STATUS_CANCELLED

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
    async def _safe_drain_state(self) -> dict[str, Any] | None:
        try:
            return await self.drain_state()
        except Exception:  # noqa: BLE001 — transient; reconcile retries next window
            logger.debug("drain-state probe failed", exc_info=True)
            return None

    async def _emit_progress(self, intent: Intent, elapsed_s: float) -> None:
        snapshot = await self._safe_drain_state() or {}
        await events.emit_manage_restart_draining(
            intent_id=intent.intent_id,
            service=intent.service,
            elapsed_s=elapsed_s,
            active_count=int(snapshot.get("active_count", 0) or 0),
            active_ops=snapshot.get("active_ops", []) or [],
        )


def build_git_worker_drain_supervisor(
    store: RestartIntentStore,
    *,
    worker_url: str,
    events_query_socket: str,
    kill: KillCaller,
    deadline_s: float = _DEFAULT_DEADLINE_S,
    idle_escalate_s: float | None = None,
) -> GitWorkerDrainSupervisor:
    """Construct a supervisor wired to the live worker + event service."""
    from transport_utils import make_async_client

    async def _begin_drain(body: dict[str, Any]) -> dict[str, Any]:
        async with make_async_client(worker_url, timeout=10.0) as client:
            resp = await client.post("/api/v1/git/admin/begin-drain", json=body)
            resp.raise_for_status()
            return resp.json()

    async def _drain_state() -> dict[str, Any]:
        async with make_async_client(worker_url, timeout=10.0) as client:
            resp = await client.get("/api/v1/git/admin/drain-state")
            resp.raise_for_status()
            return resp.json()

    async def _liveness_state() -> dict[str, Any]:
        async with make_async_client(worker_url, timeout=10.0) as client:
            resp = await client.get("/api/v1/git/cursor-auto/liveness")
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
        cancel_drain=_cancel_drain,
        deadline_s=deadline_s,
        idle_escalate_s=idle_escalate_s,
        liveness_state=_liveness_state if idle_escalate_s is not None else None,
    )


__all__ = ["GitWorkerDrainSupervisor", "build_git_worker_drain_supervisor"]
