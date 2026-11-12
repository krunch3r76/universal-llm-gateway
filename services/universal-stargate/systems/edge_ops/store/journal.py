"""Async facade over the sqlite edge-op authority store."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

from universal_event_bus import Event

from .. import config
from ..events import (
    EdgeLeasePolled,
    EdgeOpAdmitted,
    EdgeOpCompleted,
    EdgeOpFailed,
    EdgeOpLeased,
    EdgeOpProgress,
    EdgeOpReleased,
    EdgeOpStarted,
    EdgeOpStartRefused,
)
from ..model import (
    CODE_LEASE_STALE,
    CODE_TERMINAL,
    STARTED_EVENT_TYPE,
    Clock,
    EdgeOpRefusedError,
    LeaseGrant,
    OpRecord,
    Outcome,
    Redelivery,
    system_clock,
)
from ..waiter import QUEUED_CHANNEL, TRANSITION_CHANNEL, WakeRegistry
from . import journal_thread
from . import lease as lease_ops
from .admission import admit_sync, fetch_sync
from .directives_io import directives_after_sync
from .presence import edge_seen_since_sync, record_poll_sync
from .schema import initialize_schema_sync


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
        return self._path

    @property
    def waiter(self) -> WakeRegistry:
        return self._waiter

    @property
    def lease_ttl_s(self) -> float:
        return self._lease_ttl_s

    def now(self) -> float:
        return self._clock()

    def bind_event_bus(self, event_bus: EventBusProtocol | None) -> None:
        if event_bus is not None and self._event_bus is None:
            self._event_bus = event_bus

    async def initialize(self) -> None:
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
            journal_thread.emit(
                self,
                EdgeOpAdmitted(
                    op_id=record.op_id,
                    kind=record.kind,
                    op=record.op,
                    redelivery=record.redelivery,
                    deadline_epoch=record.deadline_epoch,
                ),
            )
            await self._waiter.notify(QUEUED_CHANNEL)
        return record, created

    async def edge_seen_recently(self, *, kind: str) -> bool:
        since = self.now() - config.edge_presence_window_seconds()
        return await self._in_store(edge_seen_since_sync, since, kind=kind)

    async def record_poll(self, *, edge_id: str, kinds: tuple[str, ...]) -> None:
        await self._in_store(
            record_poll_sync,
            edge_id=edge_id,
            kinds=kinds,
            now=self.now(),
        )
        journal_thread.emit(self, EdgeLeasePolled(edge_id=edge_id, kinds=list(kinds)))

    async def claim_lease(
        self,
        *,
        edge_id: str,
        kinds: tuple[str, ...],
    ) -> LeaseGrant | None:
        grant = await self._in_store(
            lease_ops.claim_lease_sync,
            edge_id=edge_id,
            kinds=kinds,
            now=self.now(),
            lease_ttl_s=self._lease_ttl_s,
        )
        if grant is not None:
            journal_thread.emit(
                self,
                EdgeOpLeased(
                    op_id=grant.op_id,
                    kind=grant.kind,
                    edge_id=edge_id,
                    lease_id=grant.lease_id,
                    lease_expires_epoch=grant.lease_expires_epoch,
                ),
            )
            await self._waiter.notify(TRANSITION_CHANNEL)
        return grant

    async def post_event(
        self,
        *,
        op_id: str,
        edge_id: str,
        lease_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[str, int] | None:
        try:
            outcome = await self._in_store(
                lease_ops.renew_and_start_sync,
                op_id=op_id,
                edge_id=edge_id,
                lease_id=lease_id,
                event_type=event_type,
                payload=payload,
                now=self.now(),
                lease_ttl_s=self._lease_ttl_s,
                started_event_type=STARTED_EVENT_TYPE,
            )
        except EdgeOpRefusedError as error:
            if event_type == STARTED_EVENT_TYPE and error.code in {
                CODE_TERMINAL,
                CODE_LEASE_STALE,
            }:
                record = await self.get(op_id)
                kind = record.kind if record is not None else "unknown"
                journal_thread.emit(
                    self,
                    EdgeOpStartRefused(
                        op_id=op_id,
                        kind=kind,
                        edge_id=edge_id,
                        refusal_code=error.code,
                    ),
                )
            raise
        if outcome is None:
            return None
        state, seq, kind = outcome
        if event_type == STARTED_EVENT_TYPE and state == "started":
            journal_thread.emit(
                self, EdgeOpStarted(op_id=op_id, kind=kind, edge_id=edge_id)
            )
        else:
            journal_thread.emit(
                self,
                EdgeOpProgress(op_id=op_id, kind=kind, event_type=event_type, seq=seq),
            )
        await self._waiter.notify(TRANSITION_CHANNEL)
        return state, seq

    async def post_result(
        self,
        *,
        op_id: str,
        edge_id: str,
        lease_id: str,
        outcome: Outcome,
        result: Any,
    ) -> str | None:
        finished = await self._in_store(
            lease_ops.finish_sync,
            op_id=op_id,
            edge_id=edge_id,
            lease_id=lease_id,
            outcome=outcome,
            result=result,
            now=self.now(),
        )
        if finished is None:
            return None
        state, kind = finished
        if state in {"completed", "failed"}:
            factory = EdgeOpFailed if state == "failed" else EdgeOpCompleted
            journal_thread.emit(self, factory(op_id=op_id, kind=kind, edge_id=edge_id))
        await self._waiter.notify(TRANSITION_CHANNEL)
        return state

    async def release(self, *, op_id: str, edge_id: str, lease_id: str) -> str | None:
        released = await self._in_store(
            lease_ops.release_sync,
            op_id=op_id,
            edge_id=edge_id,
            lease_id=lease_id,
            now=self.now(),
        )
        if released is None:
            return None
        state, kind = released
        journal_thread.emit(
            self,
            EdgeOpReleased(op_id=op_id, kind=kind, edge_id=edge_id, state=state),
        )
        await self._waiter.notify(TRANSITION_CHANNEL)
        if state == "queued":
            await self._waiter.notify(QUEUED_CHANNEL)
        return state

    async def get(self, op_id: str) -> OpRecord | None:
        return await self._in_store(fetch_sync, op_id)

    async def directives_after(self, *, op_id: str, after: int) -> list[dict[str, Any]]:
        return await self._in_store(
            directives_after_sync,
            op_id=op_id,
            after=after,
        )

    async def _in_store(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        return await journal_thread.in_store(self, operation, *args, **kwargs)
