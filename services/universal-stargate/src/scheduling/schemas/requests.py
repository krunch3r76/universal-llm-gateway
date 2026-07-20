"""Request schemas for the scheduling system. Defines the dataclass-based request records (built with `time`/`uuid` for identity and timing) that reference the `RequestStatus` enum from the sibling `resources` module to track a request's lifecycle state."""

import time
import uuid
from dataclasses import dataclass

from .resources import RequestStatus


@dataclass
class QueuedRequest:
    """A request that has been queued for processing"""

    id: str
    model_id: str
    status: RequestStatus
    created_at: float
    assigned_gateway: str | None = None
    priority: int = 0
    processing_started_at: float | None = None
    last_heartbeat: float = 0.0
    timeout_seconds: int = 300  # 5 minutes default timeout

    @classmethod
    def create(
        cls, model_id: str, priority: int = 0, timeout_seconds: int = 300
    ) -> "QueuedRequest":
        """Create a new queued request"""
        current_time = time.time()
        return cls(
            id=str(uuid.uuid4()),
            model_id=model_id,
            status=RequestStatus.QUEUED,
            created_at=current_time,
            priority=priority,
            last_heartbeat=current_time,
            timeout_seconds=timeout_seconds,
        )

    @property
    def age_seconds(self) -> float:
        """Get the age of this request in seconds"""
        return time.time() - self.created_at

    @property
    def processing_duration(self) -> float:
        """Get how long this request has been processing"""
        if self.processing_started_at:
            return time.time() - self.processing_started_at
        return 0.0

    @property
    def is_timed_out(self) -> bool:
        """Check if this request has timed out"""
        if self.status == RequestStatus.PROCESSING and self.processing_started_at:
            return self.processing_duration > self.timeout_seconds
        return False

    def update_heartbeat(self):
        """Update the last heartbeat timestamp"""
        self.last_heartbeat = time.time()

    def start_processing(self, gateway_url: str):
        """Mark the request as started processing"""
        self.status = RequestStatus.PROCESSING
        self.assigned_gateway = gateway_url
        self.processing_started_at = time.time()
        self.update_heartbeat()
