"""Request status schema for the scheduling system."""

from enum import Enum


class RequestStatus(Enum):
    """Status of a queued request"""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
