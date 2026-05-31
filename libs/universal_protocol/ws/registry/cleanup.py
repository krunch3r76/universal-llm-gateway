"""Registry cleanup - lifecycle teardown operations.

Single responsibility: Clean up stream entries (signal, close queue, unregister).
Separated from entries.py to keep that module CRUD-only.
"""

from __future__ import annotations

import asyncio

from universal_logging import get_logger

from universal_protocol.ws.queue_protocol import StreamQueueProtocol
from universal_protocol.ws.registry.entries import StreamEntry
from universal_protocol.ws.registry.protocols import CleanupHostProtocol

logger = get_logger(__name__)


# -----------------------------------------------------------------------------
# Pure helpers (no side effects except logging)
# -----------------------------------------------------------------------------


def _signal_entry_cancel(entry: StreamEntry) -> None:
    """Signal cancellation on entry (sync, idempotent).

    Inputs:
        entry: StreamEntry to signal cancellation
    """
    entry.cancel()


def _log_cleanup_complete(entry_id: str, remaining: int) -> None:
    """Log successful cleanup of an entry.

    Inputs:
        entry_id: Entry that was cleaned up
        remaining: Number of entries remaining in registry
    """
    logger.debug(f"🗑️ Cleaned up {entry_id}. Remaining: {remaining}")


def _log_queue_close_error(
    entry_id: str, error: Exception, *, is_expected: bool
) -> None:
    """Log queue close error.

    Inputs:
        entry_id: Entry whose queue failed to close
        error: The exception that occurred
        is_expected: True for expected errors (RuntimeError), False for unexpected
    """
    if is_expected:
        logger.debug(f"Queue close for {entry_id}: {error}")
    else:
        logger.warning(f"⚠️ Unexpected error closing queue for {entry_id}: {error}")


# -----------------------------------------------------------------------------
# Async I/O helpers
# -----------------------------------------------------------------------------


async def _close_queue_safe(entry_id: str, queue: StreamQueueProtocol) -> None:
    """Close queue with safe exception handling.

    Re-raises CancelledError to preserve cancellation semantics.
    Logs and suppresses operational errors (queue already closed, etc.).

    Inputs:
        entry_id: Entry identifier for logging
        queue: Queue to close (must satisfy StreamQueueProtocol)
    """
    try:
        await queue.close()
    except asyncio.CancelledError:
        raise
    except RuntimeError as e:
        _log_queue_close_error(entry_id, e, is_expected=True)
    except Exception as e:
        _log_queue_close_error(entry_id, e, is_expected=False)


# -----------------------------------------------------------------------------
# Mixin
# -----------------------------------------------------------------------------


class RegistryCleanupMixin:
    """Cleanup methods for StreamRegistry.

    Contract: Host class must satisfy CleanupHostProtocol.

    Invariant: All removal operations route through unregister() (∃! removal path).
    """

    _entries: dict[str, StreamEntry]  # Satisfied by CleanupHostProtocol

    async def cleanup_entry(self: CleanupHostProtocol, entry_id: str) -> bool:
        """Remove entry with full cleanup (idempotent).

        Orchestrates: lookup → cancel → close queue → unregister → log.

        Postcondition: entry_id ∉ registry

        Inputs:
            entry_id: Entry to clean up

        Outputs:
            True if entry found and cleaned, False if not found
        """
        entry = self._entries.get(entry_id)
        if entry is None:
            return False

        # 1. Signal cancellation (sync, idempotent)
        _signal_entry_cancel(entry)

        # 2. Close queue (async, best effort)
        if entry.queue:
            await _close_queue_safe(entry_id, entry.queue)

        # 3. Unregister via canonical path (∃! removal path)
        _ = self.unregister(entry_id)

        # 4. Log completion (segregated)
        _log_cleanup_complete(entry_id, len(self._entries))
        return True

    async def cleanup_all(self: CleanupHostProtocol) -> int:
        """Clean up all entries.

        Outputs:
            Count of entries cleaned
        """
        count = 0
        for entry_id in list(self._entries.keys()):
            if await RegistryCleanupMixin.cleanup_entry(self, entry_id):
                count += 1
        return count
