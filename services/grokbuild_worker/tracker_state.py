"""Dataclasses, exceptions, and small helpers for the grokbuild tracker.

Extracted from ``tracker.py`` in V2 close-out to bring the public-surface
tracker class under the 300-SLOC ceiling. The split is mechanical: this
module owns shape (``_Entry``), error type (``TrackerCapacityError``),
and the two pure helpers that touch nothing tracker-internal
(``_iso_now``, ``_pid_alive``).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from services.grokbuild_worker.models.async_dispatch import (
    DispatchState,
    GrokbuildDispatchRequest,
)


def iso_now() -> str:
    """Return UTC ISO-8601 timestamp with millisecond precision and ``Z`` suffix."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def pid_alive(pid: int) -> bool:
    """Return True iff ``pid`` is a live process owned by this user.

    ``PermissionError`` means the PID exists but is owned by another user
    — treat as alive (don't purge). Used by ``cleanup_orphans`` to decide
    whether to drop a stale entry on worker boot.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class TrackerCapacityError(RuntimeError):
    """Raised when the in-flight cap (operator answer 1c) is exhausted.

    Routes map this to HTTP 429 + ``Retry-After`` and emit a
    ``grokbuild.dispatch.rejected`` admission event so the observability
    contract stays uniform with other rejection codes.
    """

    def __init__(self, running: int, capacity: int) -> None:
        super().__init__(
            f"grokbuild concurrent-build cap exhausted: {running}/{capacity}"
        )
        self.running = running
        self.capacity = capacity


@dataclass
class Entry:
    """Per-dispatch tracker record (in-memory only, lost on restart).

    The ``Entry`` shape is the tracker's source of truth for a dispatch
    between admission and TTL-purge; the SSE fanout queues, the cancel
    sentinel, the pid-holder for runner cancellation, and the captured
    envelope all live here. Stored under ``GrokbuildExecutionTracker._dispatches``
    keyed by ``dispatch_id``.
    """

    dispatch_id: str
    state: DispatchState
    request: GrokbuildDispatchRequest
    pid_holder: list[int] = field(default_factory=list)
    task: asyncio.Task[Any] | None = None
    envelope: dict[str, Any] | None = None
    progress_summary: str = ""
    last_event: dict[str, Any] | None = None
    error: str | None = None
    exit_code: int | None = None
    started_at: str | None = None
    updated_at: str = field(default_factory=iso_now)
    completed_at: str | None = None
    completed_monotonic: float | None = None
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(
        default_factory=list
    )
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}

    @property
    def pid(self) -> int | None:
        if not self.pid_holder:
            return None
        candidate = self.pid_holder[0]
        return candidate if candidate > 0 else None
