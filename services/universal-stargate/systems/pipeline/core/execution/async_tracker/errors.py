"""Tracker admission error type.

``TrackerCapacityError`` is part of the package public surface: the dispatch
route (``proxy/routers/api/pipelines_dispatch.py``) maps it to HTTP 503 when the
tracker cannot admit a new execution without dropping an active one.
"""

from __future__ import annotations


class TrackerCapacityError(RuntimeError):
    """Raised when the tracker cannot admit a new execution without dropping an active
    one.

    ``register_execution`` raises it (after emitting ``pipeline.dispatch.rejected`` with
    reason ``capacity_exhausted``) when ``records`` is at ``max_records`` and no
    completed or failed record can be evicted, i.e. every slot is still running. The
    async dispatch route maps it to HTTP 503 so callers retry later instead of losing a
    live execution.
    """  # noqa: E501
