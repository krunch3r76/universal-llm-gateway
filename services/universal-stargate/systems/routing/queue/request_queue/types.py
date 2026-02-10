import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueuedRequest:
    """
    Request in the queue with metadata.

    Uses __lt__ for PriorityQueue ordering by queued_at timestamp,
    ensuring FIFO semantics even when requests are re-queued.
    """

    request_id: str
    request: dict[str, Any]
    model_id: str
    queued_at: float = field(default_factory=time.time)
    timeout: float = 300.0  # 5 minutes default
    future: asyncio.Future | None = None  # Future that resolves to the gateway
    assigned_gateway_name: str | None = None  # Gateway name for multi-gateway tracking

    def is_expired(self) -> bool:
        """Check if request has timed out."""
        return time.time() - self.queued_at > self.timeout

    def age_seconds(self) -> float:
        """Get age of request in seconds."""
        return time.time() - self.queued_at

    def __lt__(self, other):
        """Priority comparison for PriorityQueue (lower timestamp = higher priority)."""
        if not isinstance(other, QueuedRequest):
            return NotImplemented
        # Older requests (lower queued_at) have higher priority
        # This ensures FIFO ordering even after re-queuing
        return self.queued_at < other.queued_at
