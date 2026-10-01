"""Admission snap no longer reads an Auto job-state route.

Code work is ``team_dispatch``. The observer returns no row.
"""

from __future__ import annotations

from typing import Any

from claude_bundles.request_admission_census import (
    attach_claimed_job_census_rows,
    census_row_from_claimed_auto_job,
)


def fetch_claimed_job_observer(thread_id: str) -> dict[str, Any] | None:
    """Auto job-state route is gone. No observer row."""
    del thread_id
    return None


def attach_claimed_job_from_worker(
    snap: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Union a live queued or claimed Auto job on ``thread_id`` into the census."""
    observer = fetch_claimed_job_observer(thread_id)
    if observer is None:
        return snap
    status = str(observer.get("status") or "")
    row = census_row_from_claimed_auto_job(
        thread_id=str(observer.get("thread_id") or thread_id),
        job_id=str(observer.get("job_id") or ""),
        cse_registration_id=str(observer.get("cse_registration_id") or "") or None,
        source=("queued" if status == "queued" else "claimed"),
    )
    return attach_claimed_job_census_rows(snap, [row])


__all__ = [
    "attach_claimed_job_from_worker",
    "fetch_claimed_job_observer",
]
