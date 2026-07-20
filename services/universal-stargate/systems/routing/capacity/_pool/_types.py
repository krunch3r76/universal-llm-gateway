"""
Shared CapacityPool types: dataclasses, errors, protocol, and constants.

Defines CapacityToken, QueueFullError, and related structures used by mixins.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ._pool_class import CapacityPool

_WAITING_EVENT_INTERVAL_S = 15.0


class _EventBusLike(Protocol):
    def subscribe_async(self, signal: str, callback: Any) -> None: ...

    async def publish_nowait(self, event: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class _Slot:
    """Unique identifier for a (gateway, model) capacity slot."""

    gateway_id: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.gateway_id}/{self.model_id}"


@dataclass(slots=True)
class CapacityToken:
    """
    Proof of capacity reservation. Release via async context manager.

    INVARIANT: exactly one release per acquire (guarded by _released flag)
    """

    gateway_id: str
    model_id: str
    request_id: str
    queued: bool = False
    acquired_at: float = field(default_factory=time.monotonic)
    _released: bool = field(default=False, repr=False)
    _pool: CapacityPool | None = field(default=None, repr=False)

    async def release(self) -> None:
        """Return this reservation to the pool exactly once.

        The first call hands the slot back to `CapacityPool._release()` and
        marks the token as released. Later calls are no-ops, and tokens with no
        associated pool are also treated as no-ops, so callers can safely
        release in `finally` blocks without risking double accounting.
        """
        if self._released or self._pool is None:
            return
        self._released = True
        await self._pool._release(self)

    @property
    def held_ms(self) -> float:
        return (time.monotonic() - self.acquired_at) * 1000

    @asynccontextmanager
    async def __aenter__(self) -> AsyncIterator[CapacityToken]:
        yield self

    async def __aexit__(self, *_: object) -> None:
        await self.release()


@dataclass(slots=True)
class _Waiter:
    """Queued request waiting for slot assignment."""

    request_id: str
    allowed_gateway_ids: frozenset[str]
    future: asyncio.Future[str]
    queued_at: float = field(default_factory=time.monotonic)


class _WaiterCancelledError(Exception):
    """Internal signal used to wake queued requests on explicit cancellation."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class QueueFullError(Exception):
    """Raised when the FIFO queue for a model is at max depth.

    Callers should convert this to a non-retryable 503 immediately —
    the queue is overloaded and adding more waiters degrades latency
    for requests already in the queue.
    """

    def __init__(self, model_id: str, current_depth: int, max_depth: int) -> None:
        self.model_id = model_id
        self.current_depth = current_depth
        self.max_depth = max_depth
        super().__init__(
            f"Capacity queue full for {model_id} "
            f"(depth={current_depth}, max={max_depth})"
        )
