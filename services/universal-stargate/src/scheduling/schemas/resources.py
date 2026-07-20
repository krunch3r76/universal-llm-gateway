"""Request status schema for the scheduling system. Defines the `RequestStatus` enum (built on Python's `Enum`) used by the sibling `requests` module's dataclasses to represent a queued request's current lifecycle status."""

from enum import Enum


class RequestStatus(Enum):
    """Enum of the lifecycle statuses a queued request can hold as it moves through the scheduling system, from initial queuing through to its terminal outcome; referenced by the request dataclasses in the sibling `requests` module."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
