"""Tracker admission error type.

``TrackerCapacityError`` is part of the package public surface: the dispatch
route (``proxy/routers/api/pipelines_dispatch.py``) maps it to HTTP 503 when the
tracker cannot admit a new execution without dropping an active one.
"""

from __future__ import annotations


class TrackerCapacityError(RuntimeError):
    """Raised when the tracker cannot admit a new execution without dropping an active one."""  # noqa: E501
