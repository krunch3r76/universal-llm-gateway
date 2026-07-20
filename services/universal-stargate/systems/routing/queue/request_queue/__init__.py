"""
Request queue package.

Single responsibility: expose the RequestQueue API for routing/queue.
"""

from .maintenance import RequestQueueMaintenance
from .runtime import RequestQueueRuntime
from .types import QueuedRequest


class RequestQueue(RequestQueueRuntime, RequestQueueMaintenance):
    """Concrete queue: runtime enqueue/process plus maintenance shutdown drain."""

    pass


__all__ = ["RequestQueue", "QueuedRequest"]
