"""
Shared request store for federation endpoints.

INVARIANT: ∀ request r: register(r) BEFORE first_await(r)

Provides shared state between inference and cancel endpoints.
Single instance per app, injected into both routers.

Keying: Store is keyed by request_id (from X-Request-ID header).
This ID flows end-to-end from client through Master to Remote.
"""

import asyncio
import time
from dataclasses import dataclass, field

from universal_logging import get_logger

from ...common.types import RequestState

logger = get_logger(__name__)


@dataclass(slots=True)
class ActiveRequest:
    """An active request being processed."""

    request_id: str
    started_at: float = field(default_factory=time.time)
    state: RequestState = RequestState.ACTIVE
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    gateway_task: asyncio.Task | None = None


class ActiveRequestStore:
    """
    Thread-safe request store for cancellation.

    INVARIANT:
      ∀ request: register(request_id) BEFORE first_await
      ∀ terminal_state: transition ⟹ subsequent_ops_noop

    Usage:
        store = ActiveRequestStore()

        # In inference endpoint (BEFORE body parsing):
        request_id = request.headers.get(HEADER_REQUEST_ID)
        store.register(request_id)

        # In cancel endpoint (or via WebSocket):
        success = store.cancel(request_id)

        # Check in streaming loop:
        if store.is_cancelled(request_id):
            break
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._requests: dict[str, ActiveRequest] = {}
        self._ttl = ttl_seconds
        self._cleanup_task: asyncio.Task | None = None

    @property
    def active_count(self) -> int:
        """Count of active requests."""
        return sum(1 for r in self._requests.values() if r.state == RequestState.ACTIVE)

    def register(self, request_id: str) -> ActiveRequest:
        """
        Register request BEFORE first await.

        INVARIANT: register(request_id) BEFORE first_await

        This is synchronous - safe to call before any await.

        Args:
            request_id: Request ID from X-Request-ID header

        Returns:
            ActiveRequest for task attachment
        """
        req = ActiveRequest(request_id=request_id)
        self._requests[request_id] = req
        logger.debug(f"Registered request {request_id[:8]}...")
        return req

    def complete(self, request_id: str) -> None:
        """
        Mark request completed - idempotent.

        INVARIANT: transition_to(COMPLETED) ⟹ subsequent_ops_noop

        Removes request from tracking regardless of current state.
        Safe to call multiple times, safe to call on cancelled requests.

        Args:
            request_id: Request ID from X-Request-ID header
        """
        req = self._requests.pop(request_id, None)
        if req and req.state == RequestState.ACTIVE:
            req.state = RequestState.COMPLETED
            logger.debug(f"Completed request {request_id[:8]}...")

    def cancel(self, request_id: str) -> bool:
        """
        Cancel request - sets event for cooperative cancellation.

        Args:
            request_id: Request ID from X-Request-ID header

        Returns:
            True if request was found and cancelled
        """
        req = self._requests.get(request_id)
        if not req:
            return False

        if req.state != RequestState.ACTIVE:
            return True  # Already terminal

        req.state = RequestState.CANCELLED
        req.cancel_event.set()

        # Cancel the gateway task if running
        if req.gateway_task and not req.gateway_task.done():
            req.gateway_task.cancel()

        logger.info(f"Cancelled request {request_id[:8]}...")
        return True

    def is_cancelled(self, request_id: str) -> bool:
        """Check if request has been cancelled."""
        req = self._requests.get(request_id)
        return req is not None and req.cancel_event.is_set()

    def get(self, request_id: str) -> ActiveRequest | None:
        """Get request by request_id."""
        return self._requests.get(request_id)

    def cleanup_expired(self) -> int:
        """Clean up expired requests. Returns count cleaned."""
        now = time.time()
        expired = [
            rid
            for rid, req in self._requests.items()
            if now - req.started_at > self._ttl
        ]

        for rid in expired:
            if req := self._requests.pop(rid, None):
                req.state = RequestState.EXPIRED

        if expired:
            logger.warning(f"Cleaned up {len(expired)} expired requests")

        return len(expired)

    async def start_cleanup_task(self, interval_seconds: float = 60.0) -> None:
        """Start background cleanup task."""

        async def cleanup_loop():
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    self.cleanup_expired()
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")

        self._cleanup_task = asyncio.create_task(cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
