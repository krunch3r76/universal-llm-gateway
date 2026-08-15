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
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from .restart_intent_store import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
    STATUS_VERIFYING_ACTIVATION,
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
    _settle_boundary_monotonic: float | None = None

    async def supervise(self, intent: Intent) -> None:
        """Drive one intent from begin-drain to SIGTERM (or alert-only timeout).

        Cancel is observed until ``_final_epoch_check`` returns ok; after that the
        store advances to ``drained_restarting`` (kill committed) so manage cancel
        refuses. There is no cancel poll between drain.completed emit and kill —
        the refuse boundary is the final-check ok commit, not a vague "during drain".
        """
        self._settle_boundary_monotonic = None
        t0 = time.monotonic()
        deadline = t0 + self.deadline_s
        try:
            intent = await self._begin_drain(intent)
            if self._intent_cancelled(intent):
                await self._on_cancelled(intent)
                return
            outcome = await self._await_drain_completed(intent, deadline, t0)
            if outcome == _AWAIT_CANCELLED:
                await self._on_cancelled(intent)
                return
            if outcome == _AWAIT_TIMEOUT:
                await self._on_timeout(intent)
                return
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
            # Kill commit: advance BEFORE emit so cancel refuses for the entire
            # post-final-ok window (survival condition 2).
            self.store.advance(intent.intent_id, status=STATUS_DRAINED_RESTARTING)
            await events.emit_manage_restart_drain_completed(
                intent_id=intent.intent_id,
                drain_epoch=intent.drain_epoch or 0,
                worker_id=intent.worker_id,
            )
            await self._sigterm(intent, t0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — supervisor must not crash the loop
            logger.exception(
                "drain supervisor failed: intent_id=%s", intent.intent_id
            )
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
        if isinstance(started_mono, (int, float)):
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
        """Await drain convergence, deadline timeout, or store cancel.

        Returns ``converged`` | ``timeout`` | ``cancelled``. Unified loop: drains
        the (optional) event subscription for a matching ``drain.completed`` AND
        runs the ``drain-state`` reconcile check each window AND emits the
        periodic progress heartbeat — all deadline-bounded. Degrades to pure
        reconcile polling if the subscription is unavailable. Cancel is polled
        each window so manage ``cancel_restart_intent`` aborts before SIGTERM.
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
                if snapshot is not None and self._drain_state_matches(snapshot, intent):
                    return _AWAIT_CONVERGED
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
        from datetime import UTC, datetime

        from charter_runner_store.propagation_activation_events import (
            ManageRestartVerifying,
            publish_activation_event,
        )
        from charter_runner_store.propagation_validation import (
            latest_validation_for_intent,
            set_kill_boundary,
        )

        from .git_worker_activation_verify import (
            arms_activation_verify,
            schedule_activation_verify,
        )

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
        kill_boundary_at = datetime.now(UTC).isoformat()
        kill_mono = time.monotonic()
        self.store.set_kill_boundary(intent.intent_id, kill_boundary_at=kill_boundary_at)
        validation = latest_validation_for_intent(intent.intent_id)
        if validation is not None:
            set_kill_boundary(
                validation.validation_id,
                kill_boundary_at=kill_boundary_at,
                boundary_source="kill_return",
                restart_boundary_monotonic=kill_mono,
            )
        await events.emit_manage_restart_completed(
            intent_id=intent.intent_id, duration_s=time.monotonic() - t0
        )
        if arms_activation_verify(intent.action):
            if self.store.advance_if_status(
                intent.intent_id,
                from_status=STATUS_DRAINED_RESTARTING,
                to_status=STATUS_VERIFYING_ACTIVATION,
            ):
                if validation is not None:
                    publish_activation_event(
                        ManageRestartVerifying(
                            intent_id=intent.intent_id,
                            validation_id=validation.validation_id,
                            service=intent.service,
                            kill_boundary_at=kill_boundary_at,
                            boundary_source="kill_return",
                        )
                    )
                    schedule_activation_verify(
                        self.store, intent.intent_id, validation.validation_id
                    )
            return
        self.store.advance(intent.intent_id, status=STATUS_COMPLETED)

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
            "deferred git-worker restart timed out (alert-only, no kill): "
            "intent_id=%s",
            intent.intent_id,
        )

    async def _resolve_non_kill(
        self, intent: Intent, snapshot: dict[str, Any] | None
    ) -> None:
        """Final check failed: complete if the target generation is gone, else fail."""
        from datetime import UTC, datetime

        from charter_runner_store.propagation_validation import (
            latest_validation_for_intent,
            set_kill_boundary,
        )

        from .git_worker_activation_verify import (
            arms_activation_verify,
            schedule_activation_verify,
        )

        if snapshot is None or self._generation_gone(snapshot, intent):
            kill_boundary_at = datetime.now(UTC).isoformat()
            kill_mono = time.monotonic()
            self.store.set_kill_boundary(intent.intent_id, kill_boundary_at=kill_boundary_at)
            validation = latest_validation_for_intent(intent.intent_id)
            if validation is not None:
                set_kill_boundary(
                    validation.validation_id,
                    kill_boundary_at=kill_boundary_at,
                    boundary_source="generation_gone",
                    restart_boundary_monotonic=kill_mono,
                )
            if arms_activation_verify(intent.action):
                if self.store.advance_if_status(
                    intent.intent_id,
                    from_status=STATUS_PENDING_DRAIN,
                    to_status=STATUS_VERIFYING_ACTIVATION,
                ) and validation is not None:
                    schedule_activation_verify(
                        self.store, intent.intent_id, validation.validation_id
                    )
                    return
            self.store.advance(intent.intent_id, status=STATUS_COMPLETED)
            await events.emit_manage_restart_completed(intent_id=intent.intent_id, duration_s=0.0)
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
                        drain_started
                        and admitted_at
                        and admitted_at > drain_started
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

    async def _settle_propagation_ledger(
        self,
        service: str,
        *,
        settle_not_before_monotonic: float | None = None,
    ) -> None:
        """Close or fail open propagation rows from observed liveness after restart."""
        from .propagation_settle_hook import invoke_propagation_settle_for_service

        boundary = (
            settle_not_before_monotonic
            if settle_not_before_monotonic is not None
            else time.monotonic()
        )
        await invoke_propagation_settle_for_service(
            service,
            settle_not_before_monotonic=boundary,
            source="drain",
        )


# ---------------------------------------------------------------------------
# Default factory — wires the real HTTP (begin-drain/drain-state) + WS (subscribe)
# ---------------------------------------------------------------------------


def build_git_worker_drain_supervisor(
    store: RestartIntentStore,
    *,
    worker_url: str,
    events_query_socket: str,
    kill: KillCaller,
    deadline_s: float = _DEFAULT_DEADLINE_S,
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
    )
