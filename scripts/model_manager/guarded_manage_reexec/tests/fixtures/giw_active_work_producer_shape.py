"""Producer-shaped ``/active-work`` body for dry-run foreign-queue tests.

Key → producer (lane tip that cited these lines):

- ``running``, ``queued`` (int): ``integrate.py:495–496`` assigned, ``:521–522``
  in the JSON body. Top-level ``queued`` is ``_GATE.queue_length``, never a list.
- ``cursor_dispatches``: ``integrate.py:497`` / ``:523``.
- ``cursor_sdk_gate``: ``integrate.py`` from ``sdk_dispatch_gate_stats()``
  then ``sdk_gate["holders"]`` (standard and operator id lists). ``live_writers``
  is the A+B count on that stats object. ``busy_status`` is attached inside
  ``sdk_dispatch_gate_stats``. Envelope fields ``active_holder``,
  ``active_holders``, ``queue_depth``, ``queued_dispatches`` use ``dispatch_id``
  on queued rows.
- ``write_lease``: ``integrate.py:506`` / ``:525`` from ``lease_snapshot``.
  ``holder_dispatch_id`` is ``_holder_projection`` (``cursor_dispatch_ledger.py:407``).
  ``active_holders`` at ``:2670`` (same projection, ``holder_dispatch_id``).
  ``queue_depth`` at ``:2674``. ``queued`` row dicts at ``:2659–2667``, returned
  at ``:2675`` (id key ``dispatch_id``).
- ``active_count``: ``integrate.py:509`` / ``:526``.
- ``active_ops``: ``integrate.py:527``. Row shape ``admission.py:88–94``
  (``kind``, ``op_id``, ``route``, ``admitted_at``, ``state``). For
  ``kind == "cursor_sdk"``, ``op_id`` is the dispatch id
  (``cursor_dispatch_ledger.py:2886–2887``).
- ``cse_holders``, ``cse_holder``, ``busy``: ``integrate.py:528–530``.
"""

from __future__ import annotations

from typing import Any

_HOLDER_SELF: dict[str, Any] = {
    "holder_dispatch_id": "d-self",
    "holder_thread_id": "t-self",
    "holder_resolved_model": None,
    "holder_subject_preview": None,
    "holder_status": "running",
    "holder_started_at": None,
    "holder_last_heartbeat_at": None,
}

_QUEUED_LEASE_ROW: dict[str, Any] = {
    "dispatch_id": "d-other",
    "thread_id": "t-other",
    "queue_position": 1,
    "lease_key": "lane-b",
    "queued_on": "write_lease:lane-b",
}

_QUEUED_GATE_ROW: dict[str, Any] = {
    "dispatch_id": "d-other",
    "thread_id": "t-other",
    "queue_position": 1,
    "lease_key": "lane-b",
}

PRODUCER_ACTIVE_WORK_FOREIGN_QUEUED: dict[str, Any] = {
    "running": 1,
    "queued": 1,
    "cursor_dispatches": [],
    "cursor_sdk_gate": {
        "holders": {"standard": ["d-self"], "operator": []},
        "live_writers": 1,
        "busy_status": {
            "active_holder": {
                "dispatch_id": "d-self",
                "thread_id": "t-self",
                "model": None,
                "subject_preview": None,
                "status": "running",
            },
            "active_holders": [_HOLDER_SELF],
            "queue_depth": 1,
            "queued_dispatches": [_QUEUED_GATE_ROW],
        },
    },
    "write_lease": {
        "holder_dispatch_id": "d-self",
        "holder_thread_id": "t-self",
        "holder_resolved_model": None,
        "holder_subject_preview": None,
        "holder_status": "running",
        "holder_started_at": None,
        "holder_last_heartbeat_at": None,
        "active_holders": [_HOLDER_SELF],
        "holder_source_repo": None,
        "queue_depth": 1,
        "queued": [_QUEUED_LEASE_ROW],
    },
    "active_count": 1,
    "active_ops": [
        {
            "kind": "cursor_sdk",
            "op_id": "d-self",
            "route": "cursor_sdk",
            "admitted_at": "2026-10-14T00:00:00+00:00",
            "state": "running",
        }
    ],
    "cse_holders": [],
    "cse_holder": None,
    "busy": True,
}
