"""Async facade over the sqlite edge-op authority store.

One object owns the authority: the journal path, the clock, the event bus and
the ``asyncio.to_thread`` boundary that keeps sqlite off the event loop. Every
read and write folds due transitions first (``fold_due_sync``), so expiry and
lease loss are decided on access instead of by a background sweeper, and a
second ``EdgeOpsJournal`` opened on the same path after a restart folds the
same states from the same stored timestamps.

The clock is injected so tests can move time without sleeping. Lifecycle
events are published fire-and-forget; they are advisory, and a dropped publish
never changes what the store says.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

from universal_event_bus import Event
from universal_logging import get_logger

from .. import config
from ..events import (
    EdgeLeasePolled,
    EdgeOpAdmitted,
    EdgeOpCompleted,
    EdgeOpExpired,
    EdgeOpFailed,
    EdgeOpLeased,
    EdgeOpProgress,
    EdgeOpReleased,
    EdgeOpRequeued,
    EdgeOpStarted,
    EdgeOpUnknown,
)
from ..model import (
    STARTED_EVENT_TYPE,
    Clock,
    LeaseGrant,
    OpRecord,
    Outcome,
    Redelivery,
    system_clock,
)
from ..waiter import QUEUED_CHANNEL, TRANSITION_CHANNEL, WakeRegistry
from . import lease as lease_ops
from .admission import admit_sync, fetch_sync
from .schema import (
    ensure_schema_sync,
    fold_due_sync,
    initialize_schema_sync,
    open_connection,
)

logger = get_logger(__name__)

_FOLD_EVENT_FACTORIES = {
    "expired": EdgeOpExpired,
    "queued": EdgeOpRequeued,
    "unknown": EdgeOpUnknown,
}


class EventBusProtocol(Protocol):
    """The one event-bus method this store uses."""

    async def publish_nowait(self, event: Event) -> Any: ...


class EdgeOpsJournal:
    """Journal-backed authority for edge-op state."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        clock: Clock = system_clock,
        event_bus: EventBusProtocol | None = None,
        waiter: WakeRegistry | None = None,
        lease_ttl_s: float = config.LEASE_TTL_SECONDS,
    ) -> None:
        self._path = path if path is not None else config.journal_path()
        self._clock = clock
        self._event_bus = event_bus
        self._waiter = waiter if waiter is not None else WakeRegistry()
        self._lease_ttl_s = lease_ttl_s
        self._schema_ready = False

    @property
    def path(self) -> Path:
        """Where this journal's authority lives on disk."""
        return self._path

    @property
    def waiter(self) -> WakeRegistry:
        """The advisory wake registry the long-polls park on."""
        return self._waiter

    @property
    def lease_ttl_s(self) -> float:
        """Lease time to live granted to an edge, in seconds."""
        return self._lease_ttl_s

    def now(self) -> float:
        """Current time from the injected clock."""
        return self._clock()

    def bind_event_bus(self, event_bus: EventBusProtocol | None) -> None:
        """Adopt the process event bus when this journal has none yet.

        The fallback journal the routes use is built on the first request that
        reaches the store, which can be before anything has a bus to hand it,
        so the bus is injected on first touch instead of being fixed at
        construction. Idempotent, and it never replaces a bus already
        injected: a journal that a boot sequence or a test constructed with
        one keeps that one.

        Args:
            event_bus: The bus to adopt, or ``None`` when none is available
                yet — then this call changes nothing and a later one can
                still bind.
        """
        if event_bus is not None and self._event_bus is None:
            self._event_bus = event_bus

    async def initialize(self) -> None:
        """Create the schema if missing. Idempotent; safe on every boot."""
        await asyncio.to_thread(initialize_schema_sync, self._path)
        self._schema_ready = True

    async def admit(
        self,
        *,
        kind: str,
        op: str,
        args: dict[str, Any],
        redelivery: Redelivery,
        idempotency_key: str,
        deadline_s: float,
    ) -> tuple[OpRecord, bool]:
        """Queue an op, or return the live row for a repeated caller key."""
        now = self.now()
        deadline_epoch = now + deadline_s
        record, created = await self._in_store(
            admit_sync,
            kind=kind,
            op=op,
            args=args,
            redelivery=redelivery,
            idempotency_key=idempotency_key,
            now=now,
            deadline_epoch=deadline_epoch,
            dedupe_until_epoch=deadline_epoch + config.DEDUPE_GRACE_SECONDS,
        )
        if created:
            self._emit(
                EdgeOpAdmitted(
                    op_id=record.op_id,
                    kind=record.kind,
                    op=record.op,
                    redelivery=record.redelivery,
                    deadline_epoch=record.deadline_epoch,
                )
            )
            await self._waiter.notify(QUEUED_CHANNEL)
        return record, created

    async def edge_seen_recently(self, *, kind: str) -> bool:
        """True when an edge serving *kind* polled inside the presence window."""
        since = self.now() - config.edge_presence_window_seconds()
        return await self._in_store(lease_ops.edge_seen_since_sync, since, kind=kind)

    async def record_poll(self, *, edge_id: str, kinds: tuple[str, ...]) -> None:
        """Stamp this edge's presence for the ``edge_unavailable`` check."""
        await self._in_store(
            lease_ops.record_poll_sync,
            edge_id=edge_id,
            kinds=kinds,
            now=self.now(),
        )
        self._emit(EdgeLeasePolled(edge_id=edge_id, kinds=list(kinds)))

    async def claim_lease(
        self,
        *,
        edge_id: str,
        kinds: tuple[str, ...],
    ) -> LeaseGrant | None:
        """Claim the oldest queued op this edge serves, or ``None``."""
        grant = await self._in_store(
            lease_ops.claim_lease_sync,
            edge_id=edge_id,
            kinds=kinds,
            now=self.now(),
            lease_ttl_s=self._lease_ttl_s,
        )
        if grant is not None:
            self._emit(
                EdgeOpLeased(
                    op_id=grant.op_id,
                    kind=grant.kind,
                    edge_id=edge_id,
                    lease_id=grant.lease_id,
                    lease_expires_epoch=grant.lease_expires_epoch,
                )
            )
            await self._waiter.notify(TRANSITION_CHANNEL)
        return grant

    async def post_event(
        self,
        *,
        op_id: str,
        edge_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[str, int] | None:
        """Append an edge event, renewing the lease. ``None`` on a lease mismatch.

        Raises:
            EdgeOpRefusedError: 409 ``edge_op_terminal`` when a ``started``
                event arrives for an op that is already in a terminal state.
        """
        outcome = await self._in_store(
            lease_ops.renew_and_start_sync,
            op_id=op_id,
            edge_id=edge_id,
            event_type=event_type,
            payload=payload,
            now=self.now(),
            lease_ttl_s=self._lease_ttl_s,
            started_event_type=STARTED_EVENT_TYPE,
        )
        if outcome is None:
            return None
        state, seq, kind = outcome
        if event_type == STARTED_EVENT_TYPE and state == "started":
            self._emit(EdgeOpStarted(op_id=op_id, kind=kind, edge_id=edge_id))
        else:
            self._emit(
                EdgeOpProgress(op_id=op_id, kind=kind, event_type=event_type, seq=seq)
            )
        await self._waiter.notify(TRANSITION_CHANNEL)
        return state, seq

    async def post_result(
        self,
        *,
        op_id: str,
        edge_id: str,
        outcome: Outcome,
        result: Any,
    ) -> str | None:
        """Store the result verbatim and close the op. ``None`` on a mismatch."""
        finished = await self._in_store(
            lease_ops.finish_sync,
            op_id=op_id,
            edge_id=edge_id,
            outcome=outcome,
            result=result,
            now=self.now(),
        )
        if finished is None:
            return None
        state, kind = finished
        if state in {"completed", "failed"}:
            factory = EdgeOpFailed if state == "failed" else EdgeOpCompleted
            self._emit(factory(op_id=op_id, kind=kind, edge_id=edge_id))
        await self._waiter.notify(TRANSITION_CHANNEL)
        return state

    async def release(self, *, op_id: str, edge_id: str) -> str | None:
        """Release a lease without a result. ``None`` on a lease mismatch."""
        released = await self._in_store(
            lease_ops.release_sync,
            op_id=op_id,
            edge_id=edge_id,
            now=self.now(),
        )
        if released is None:
            return None
        state, kind = released
        self._emit(EdgeOpReleased(op_id=op_id, kind=kind, edge_id=edge_id, state=state))
        await self._waiter.notify(TRANSITION_CHANNEL)
        if state == "queued":
            await self._waiter.notify(QUEUED_CHANNEL)
        return state

    async def get(self, op_id: str) -> OpRecord | None:
        """Folded record for *op_id* after applying due transitions."""
        return await self._in_store(fetch_sync, op_id)

    async def directives_after(self, *, op_id: str, after: int) -> list[dict[str, Any]]:
        """Directives for *op_id* with a sequence number above *after*."""
        return await self._in_store(
            lease_ops.directives_after_sync,
            op_id=op_id,
            after=after,
        )

    async def _in_store(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Run *operation* on a worker thread with due transitions folded first.

        The folds publish from a ``finally`` because a store operation may
        refuse: a ``started`` ack arriving after its op folded ``expired``
        answers 409, and that fold still owes its lifecycle event and its
        transition wake-up. A caller parked in ``await_result`` is waiting on
        exactly that notify, so dropping it would leave the caller holding its
        whole budget open while the authority had already said ``expired``.
        """
        folded: list[tuple[str, str, str, str]] = []
        try:
            return await asyncio.to_thread(
                self._fold_then, operation, args, kwargs, folded
            )
        finally:
            await self._publish_folds(folded)

    async def _publish_folds(self, folded: list[tuple[str, str, str, str]]) -> None:
        """Emit one lifecycle event per folded row and wake the long-polls."""
        if not folded:
            return
        for op_id, kind, state, reason in folded:
            factory = _FOLD_EVENT_FACTORIES.get(state)
            if factory is not None:
                self._emit(factory(op_id=op_id, kind=kind, reason=reason))
        await self._waiter.notify(TRANSITION_CHANNEL)
        if any(state == "queued" for _, _, state, _ in folded):
            await self._waiter.notify(QUEUED_CHANNEL)

    def _fold_then(
        self,
        operation: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        folded: list[tuple[str, str, str, str]],
    ) -> Any:
        """Fold due transitions into *folded*, then run *operation*.

        The fold list is an out-parameter rather than a second return value so
        that the caller still holds it when *operation* raises: the fold is
        committed by then, and its events are owed either way.

        The schema is ensured once per instance rather than assumed, because a
        journal can be reached by a request before any boot sequence called
        ``initialize`` — a missing table would otherwise surface as a 500 on
        the first edge call after a fresh install.
        """
        with open_connection(self._path) as connection:
            if not self._schema_ready:
                ensure_schema_sync(connection)
                self._schema_ready = True
            folded.extend(fold_due_sync(connection, self._clock()))
            return operation(connection, *args, **kwargs)

    def _emit(self, event: Event) -> None:
        """Fire-and-forget publish. Events are advisory, so failures only log."""
        if self._event_bus is None:
            return
        try:
            asyncio.create_task(self._event_bus.publish_nowait(event))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to publish edge-op event: %s", exc)
