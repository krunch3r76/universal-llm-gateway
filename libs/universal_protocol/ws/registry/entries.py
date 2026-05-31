"""Stream entry store - CRUD operations only.

Single responsibility: Manage stream/request entries (register, get, unregister).
No cleanup logic, no control plane, no idle monitoring.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

from universal_logging import get_logger

from universal_protocol.ws.lifecycle import StreamContext
from universal_protocol.ws.queue_protocol import StreamQueueProtocol

logger = get_logger(__name__)

EntryKind = Literal["stream", "request"]


@dataclass(slots=True)
class StreamEntry:
    """Single source of truth for stream/request state.

    Invariants:
      ∀ entry, entry.cancellation_event ∈ asyncio.Event
      ∧ entry.cleanup_complete_event ∈ asyncio.Event
      ∧ entry.cleanup_complete_event.is_set() ⟹ stream teardown finished
      ∀ entry where entry.kind = "stream":
        entry.task ≠ None ⟹ task is streaming generator
    """

    entry_id: str
    kind: EntryKind
    cancellation_event: asyncio.Event
    cleanup_complete_event: asyncio.Event
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    context: StreamContext | None = None
    queue: StreamQueueProtocol | None = None
    task: asyncio.Task | None = None

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def is_idle(self, timeout_seconds: float) -> bool:
        """Check if entry has been idle beyond timeout."""
        return time.time() - self.last_activity > timeout_seconds

    def cancel(self) -> None:
        """Signal cancellation (idempotent - safe to call multiple times)."""
        self.cancellation_event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was signaled."""
        return self.cancellation_event.is_set()

    def mark_cleanup_complete(self) -> None:
        """Signal cleanup completion (idempotent).

        Postcondition: cleanup_complete_event.is_set() = True
        """
        self.cleanup_complete_event.set()

    async def wait_for_cleanup(self, timeout: float) -> bool:
        """Wait for cleanup to complete with timeout.

        Inputs:
            timeout: Maximum seconds to wait

        Outputs:
            True if cleanup completed, False if timeout

        Non-blocking: Uses asyncio.wait_for with timeout, no re-raise on TimeoutError
        """
        try:
            await asyncio.wait_for(self.cleanup_complete_event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False


class StreamRegistry:
    """Entry store for stream/request state.

    Single responsibility: CRUD operations on entries.

    Invariants:
      ∀ entry_id: |{e | e.entry_id = entry_id}| ≤ 1 (at most one entry)
      Registration/unregistration are atomic (no await between check and modify)

    Thread-safety:
      Single-threaded async - no locks needed (Python dict ops are atomic)
    """

    __slots__ = (
        "_entries",
        "_idle_monitor_task",
        "_idle_timeout",
        "_idle_check_interval",
    )

    def __init__(self) -> None:
        self._entries: dict[str, StreamEntry] = {}
        # Slots for mixins (initialized here, used by mixins)
        self._idle_monitor_task: asyncio.Task[None] | None = None
        self._idle_timeout: float = 30.0
        self._idle_check_interval: float = 5.0

    def register(
        self,
        entry_id: str,
        kind: EntryKind,
        context: StreamContext | None = None,
        queue: StreamQueueProtocol | None = None,
        task: asyncio.Task | None = None,
    ) -> StreamEntry:
        """Register a new stream/request entry.

        Precondition: entry_id ∉ registry
        Postcondition: entry_id ∈ registry ∧ entry.kind = kind

        Raises:
            ValueError: If entry_id already exists
        """
        if entry_id in self._entries:
            raise ValueError(f"Entry {entry_id} already registered")

        entry = StreamEntry(
            entry_id=entry_id,
            kind=kind,
            cancellation_event=asyncio.Event(),
            cleanup_complete_event=asyncio.Event(),
            context=context,
            queue=queue,
            task=task,
        )
        self._entries[entry_id] = entry
        logger.debug(
            f"✅ Registered {kind} entry {entry_id}. Total: {len(self._entries)}"
        )
        return entry

    def get(self, entry_id: str) -> StreamEntry | None:
        """Get entry by ID. Returns None if not found."""
        return self._entries.get(entry_id)

    def unregister(self, entry_id: str) -> StreamEntry | None:
        """Unregister and return entry. Returns None if not found.

        Postcondition: entry_id ∉ registry
        """
        entry = self._entries.pop(entry_id, None)
        if entry:
            logger.debug(f"🗑️ Unregistered {entry_id}. Remaining: {len(self._entries)}")
        return entry

    def update_activity(self, entry_id: str) -> bool:
        """Update activity timestamp. Returns False if not found."""
        entry = self._entries.get(entry_id)
        if entry:
            entry.update_activity()
            return True
        return False

    def get_idle_entries(self, timeout_seconds: float) -> list[StreamEntry]:
        """Get all entries that have exceeded idle timeout."""
        return [e for e in self._entries.values() if e.is_idle(timeout_seconds)]

    def clear(self) -> None:
        """Clear all entries without cleanup (for testing only)."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, entry_id: str) -> bool:
        return entry_id in self._entries

    def __iter__(self):
        return iter(self._entries)

    def items(self):
        return self._entries.items()

    def keys(self):
        return self._entries.keys()

    def values(self):
        return self._entries.values()
