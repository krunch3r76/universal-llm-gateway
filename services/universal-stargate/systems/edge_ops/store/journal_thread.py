"""Thread boundary and fold publication for ``EdgeOpsJournal``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from universal_event_bus import Event
from universal_logging import get_logger

from ..events import EdgeOpExpired, EdgeOpRequeued, EdgeOpUnknown
from ..waiter import QUEUED_CHANNEL, TRANSITION_CHANNEL
from .schema import ensure_schema_sync, fold_due_sync, open_connection

if TYPE_CHECKING:
    from .journal import EdgeOpsJournal

logger = get_logger(__name__)

_FOLD_EVENT_FACTORIES = {
    "expired": EdgeOpExpired,
    "queued": EdgeOpRequeued,
    "unknown": EdgeOpUnknown,
}


async def in_store(
    journal: EdgeOpsJournal,
    operation: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run *operation* on a worker thread with due transitions folded first."""
    folded: list[tuple[str, str, str, str]] = []
    try:
        return await asyncio.to_thread(
            fold_then, journal, operation, args, kwargs, folded
        )
    finally:
        await publish_folds(journal, folded)


async def publish_folds(
    journal: EdgeOpsJournal,
    folded: list[tuple[str, str, str, str]],
) -> None:
    """Emit one lifecycle event per folded row and wake the long-polls."""
    if not folded:
        return
    for op_id, kind, state, reason in folded:
        factory = _FOLD_EVENT_FACTORIES.get(state)
        if factory is not None:
            emit(journal, factory(op_id=op_id, kind=kind, reason=reason))
    await journal.waiter.notify(TRANSITION_CHANNEL)
    if any(state == "queued" for _, _, state, _ in folded):
        await journal.waiter.notify(QUEUED_CHANNEL)


def fold_then(
    journal: EdgeOpsJournal,
    operation: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    folded: list[tuple[str, str, str, str]],
) -> Any:
    """Fold due transitions into *folded*, then run *operation*."""
    with open_connection(journal.path) as connection:
        if not journal._schema_ready:
            ensure_schema_sync(connection)
            journal._schema_ready = True
        folded.extend(fold_due_sync(connection, journal._clock()))
        return operation(connection, *args, **kwargs)


def emit(journal: EdgeOpsJournal, event: Event) -> None:
    """Fire-and-forget publish. Events are advisory, so failures only log."""
    if journal._event_bus is None:
        return
    try:
        asyncio.create_task(journal._event_bus.publish_nowait(event))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to publish edge-op event: %s", exc)
