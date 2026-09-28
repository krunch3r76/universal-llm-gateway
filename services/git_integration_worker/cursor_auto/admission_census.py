"""Job-keyed request-admission census for same-thread claimed Auto jobs."""

from __future__ import annotations

from typing import Any

from claude_bundles.request_admission_census import (
    attach_claimed_job_census_rows,
    census_row_from_claimed_auto_job,
    claimed_auto_job_counts_for_census,
)

from services.git_integration_worker.cursor_auto.job_lifecycle import (
    observer_view_from_row,
)


def census_row_from_ledger_row(row: Any) -> dict[str, Any] | None:
    """Build a census identity row from a ``cursor_auto_jobs`` SQLite row."""
    view = observer_view_from_row(row)
    if not claimed_auto_job_counts_for_census(view):
        return None
    reg: str | None = None
    raw = row["record_json"] if "record_json" in row.keys() else None
    if raw:
        try:
            import json

            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            reg = str(data.get("cse_registration_id") or "").strip() or None
    status = str(view.get("status") or "")
    return census_row_from_claimed_auto_job(
        thread_id=str(view.get("thread_id") or ""),
        job_id=str(view.get("job_id") or ""),
        cse_registration_id=reg,
        source=("cursor-auto-queued" if status == "queued" else "cursor-auto-claimed"),
    )


def attach_claimed_job_from_ledger(
    snap: dict[str, Any],
    *,
    thread_id: str,
) -> dict[str, Any]:
    """Attach the incumbent queued or claimed job for ``thread_id`` when present."""
    tid = (thread_id or "").strip()
    if not tid:
        return snap
    from services.git_integration_worker.cursor_auto.job_ledger import get_ledger

    view = get_ledger().observer_state(thread_id=tid)
    if view is None or not claimed_auto_job_counts_for_census(view):
        return snap
    status = str(view.get("status") or "")
    row = census_row_from_claimed_auto_job(
        thread_id=tid,
        job_id=str(view.get("job_id") or ""),
        cse_registration_id=None,
        source=("cursor-auto-queued" if status == "queued" else "cursor-auto-claimed"),
    )
    return attach_claimed_job_census_rows(snap, [row])


__all__ = [
    "attach_claimed_job_from_ledger",
    "census_row_from_ledger_row",
]
