"""Stream lifecycle management - worker-side wrapper.

Provides metrics-aware wrappers around StreamRegistry operations.
All cleanup paths (cancel, unload, idle, done) use these helpers.

Ownership:
  - StreamRegistry (libs): State storage + idempotent cleanup
  - stream_lifecycle (services): Metrics + worker-specific logging

Invariants:
  ∀ entry ∈ stream_registry: entry.kind ∈ {"stream", "request"}
  Cleanup: signal ∧ close_queue ∧ unregister ∧ metrics
"""

import asyncio

from universal_logging import get_logger
from universal_protocol.observability import (
    end_stream,
    set_streams_active,
    start_stream,
)
from universal_protocol.ws.lifecycle import StreamContext
from universal_protocol.ws.registry import EntryKind, stream_registry
from universal_protocol.ws.stream_queue import UnboundedStreamQueue

logger = get_logger(__name__)


def register_stream_entry(
    entry_id: str,
    kind: EntryKind,
    context: StreamContext | None = None,
    queue: UnboundedStreamQueue | None = None,
    task: asyncio.Task | None = None,
) -> asyncio.Event:
    """Register entry in stream_registry with metrics.

    Returns:
        cancellation_event from the registered entry

    Side-effects:
        - Entry added to stream_registry
        - set_streams_active() called
        - start_stream() called
    """
    entry = stream_registry.register(
        entry_id=entry_id,
        kind=kind,
        context=context,
        queue=queue,
        task=task,
    )

    # Metrics (sync, non-blocking)
    set_streams_active(len(stream_registry))
    start_stream(entry_id)

    logger.info(
        f"✅ [stream_lifecycle] Registered {kind} {entry_id}. "
        f"Total: {len(stream_registry)}"
    )
    return entry.cancellation_event


async def cleanup_stream_entry(entry_id: str, reason: str = "cleanup") -> bool:
    """Remove entry from stream_registry with metrics.

    Idempotent: Safe to call multiple times.

    Postcondition: entry_id ∉ stream_registry
    """
    # Delegate to registry's idempotent cleanup
    cleaned = await stream_registry.cleanup_entry(entry_id)

    if cleaned:
        # Metrics (sync, non-blocking)
        set_streams_active(len(stream_registry))
        end_stream(entry_id)
        logger.info(f"🧹 [stream_lifecycle] Cleaned up {entry_id} ({reason})")

    return cleaned


async def cleanup_all_streams(reason: str = "unload") -> int:
    """Clean up all entries with metrics.

    Used by: handle_unload_model
    """
    count = 0
    for entry_id in list(stream_registry.keys()):
        if await cleanup_stream_entry(entry_id, reason):
            count += 1
    return count


async def notify_model_unload(entry_id: str, model_name: str) -> bool:
    """Notify stream of model unload via registry.

    Delegates to stream_registry.notify_model_unload() for consistency.
    """
    from universal_protocol.ws.registry import stream_registry

    return stream_registry.notify_model_unload(entry_id, model_name)
