"""
Data schemas for the scheduling system.
Updated for new API response formats.
"""

from .requests import QueuedRequest
from .resources import RequestStatus

__all__ = ["RequestStatus", "QueuedRequest"]
