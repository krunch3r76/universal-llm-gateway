"""
Request queue system with resource verification and backpressure.

Event-driven architecture:
- model.execution.completed events wake queue processors immediately
- REQUEST_QUEUED, REQUEST_TIMEOUT, REQUEST_REMOVED events for metrics
- GATEWAY_RESOURCE_UPDATE invalidates verification cache
"""

from .request_queue import QueuedRequest, RequestQueue
from .verification import ResourceVerifier, VerificationResult

__all__ = [
    # Request queue
    "RequestQueue",
    "QueuedRequest",
    # Resource verification
    "ResourceVerifier",
    "VerificationResult",
]
