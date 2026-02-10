"""
Rate limiting middleware for WebSocket connections.

Event-Driven Cleanup:
    Each client entry manages its own expiration via asyncio.Task.
    No periodic cleanup loop - expiration tasks fire after inactivity.
"""

import asyncio
import time
from dataclasses import dataclass, field

from universal_logging import get_logger

logger = get_logger(__name__)

# Stale threshold for client entries (1 hour)
STALE_THRESHOLD = 3600


@dataclass
class RateLimitInfo:
    """
    Track rate limit information for a client with self-managing expiration.

    Each client entry has its own expiration task instead of
    relying on a periodic cleanup loop.
    """

    client_id: str = ""
    request_count: int = 0
    window_start: float = field(default_factory=time.time)
    last_request: float = field(default_factory=time.time)

    # For token bucket algorithm
    tokens: float = 100.0  # Start with full bucket
    last_refill: float = field(default_factory=time.time)

    # Per-client expiration management
    _expiration_task: asyncio.Task | None = field(default=None, repr=False)
    _on_expire: object = field(default=None, repr=False)

    def schedule_expiration(self, ttl: float, on_expire) -> None:
        """Schedule expiration after inactivity."""
        self._on_expire = on_expire
        self._cancel_expiration()  # Cancel any existing

        self._expiration_task = asyncio.create_task(
            self._expire_after_ttl(ttl),
            name=f"ratelimit-expire-{self.client_id[:16]}",
        )

    async def _expire_after_ttl(self, ttl: float) -> None:
        """Expire after inactivity."""
        try:
            await asyncio.sleep(ttl)
            if self._on_expire:
                self._on_expire(self)
        except asyncio.CancelledError:
            pass

    def _cancel_expiration(self) -> None:
        """Cancel pending expiration."""
        if self._expiration_task and not self._expiration_task.done():
            self._expiration_task.cancel()
            self._expiration_task = None

    def refresh_expiration(self, ttl: float) -> None:
        """Reset expiration timer on activity."""
        self.schedule_expiration(ttl, self._on_expire)


class WebSocketRateLimiter:
    """
    Rate limiter for WebSocket connections using token bucket algorithm.

    Event-Driven Architecture:
        No cleanup loop. Each client entry manages its own
        expiration via asyncio.Task, resetting on each request.
    """

    def __init__(
        self,
        max_requests_per_minute: int = 60,
        max_burst: int = 20,
    ):
        self.max_requests_per_minute = max_requests_per_minute
        self.max_burst = max_burst
        self.tokens_per_second = max_requests_per_minute / 60.0

        # Track rate limits by client identifier (IP or API key)
        self._clients: dict[str, RateLimitInfo] = {}

    async def start(self):
        """Start the rate limiter (nothing to start - per-client expiration)."""
        logger.info("Rate limiter started (per-client expiration)")

    async def check_rate_limit(self, client_id: str, cost: float = 1.0) -> bool:
        """
        Check if a client has exceeded rate limits.

        Args:
            client_id: Unique identifier for the client (IP or API key)
            cost: Cost of this request in tokens (default: 1.0)

        Returns:
            True if request is allowed, False if rate limited
        """
        current_time = time.time()

        if client_id not in self._clients:
            # Create new client entry with expiration
            info = RateLimitInfo(
                client_id=client_id,
                tokens=self.max_burst,
                last_refill=current_time,
                last_request=current_time,
            )
            info.schedule_expiration(STALE_THRESHOLD, self._on_client_expired)
            self._clients[client_id] = info
        else:
            info = self._clients[client_id]

        # Refill tokens based on time elapsed
        time_elapsed = current_time - info.last_refill
        tokens_to_add = time_elapsed * self.tokens_per_second

        # Cap tokens at max_burst
        info.tokens = min(self.max_burst, info.tokens + tokens_to_add)
        info.last_refill = current_time
        info.last_request = current_time

        # Reset expiration timer on activity
        info.refresh_expiration(STALE_THRESHOLD)

        # Check if we have enough tokens
        if info.tokens >= cost:
            info.tokens -= cost
            info.request_count += 1
            return True

        # Rate limited
        logger.warning(
            f"Rate limit exceeded for client {client_id}: "
            f"tokens={info.tokens:.2f}, cost={cost}"
        )
        return False

    def _on_client_expired(self, info: RateLimitInfo) -> None:
        """Called when a client entry expires."""
        if info.client_id in self._clients:
            del self._clients[info.client_id]
            logger.debug(f"Rate limit entry expired: {info.client_id[:16]}")

    async def get_client_status(self, client_id: str) -> dict[str, float]:
        """Get current rate limit status for a client."""
        info = self._clients.get(client_id)
        if not info:
            return {
                "tokens": self.max_burst,
                "max_tokens": self.max_burst,
                "refill_rate": self.tokens_per_second,
            }

        # Calculate current tokens
        current_time = time.time()
        time_elapsed = current_time - info.last_refill
        tokens_to_add = time_elapsed * self.tokens_per_second
        current_tokens = min(self.max_burst, info.tokens + tokens_to_add)

        return {
            "tokens": current_tokens,
            "max_tokens": self.max_burst,
            "refill_rate": self.tokens_per_second,
            "last_request": info.last_request,
            "request_count": info.request_count,
        }

    async def shutdown(self):
        """Shutdown the rate limiter and cleanup."""
        # Cancel all client expiration tasks
        for info in self._clients.values():
            info._cancel_expiration()
        self._clients.clear()
        logger.info("Rate limiter shutdown complete")


# Global rate limiter instance
websocket_rate_limiter = WebSocketRateLimiter()
