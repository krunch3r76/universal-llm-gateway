"""Drain occupancy carve-out for Auto jobs waiting on park_for_restart resume.

``claimed_occupancy_ops`` must keep counting a live nested SDK (9470: SIGTERM
beat CLOSEOUT). A nested SDK already ``cancelled`` + ``park_for_restart`` is
the opposite: the job is waiting for ``resume_of``, which only admits after
GIW restart. Counting it prevents that restart (specimen 2026-09-20
AutoJob 765c56f3 / nested ``auto-a479e8e67316``).

Do not require ``park_intent_id`` to equal the live drain intent.
``recycle_giw`` can replace the intent (1f0a855c → e3dc936c, ``park_live``
false) without restamping the park row; resume_of is still startup-only.

Do not treat ledger ``cancelled`` as nested-poll terminal — that drops the
Auto job before the resume child exists.
"""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_RESTART,
    ParkRow,
    load_park_row,
    park_projection,
)


def waiting_park_resume_for_intent(
    *,
    job_id: str,
    intent_id: str | None,
    relay_state: dict[str, Any] | None = None,
    park_row: ParkRow | None = None,
) -> bool:
    """True when this claimed Auto job's nested SDK is parked for restart.

    *intent_id* is the live drain generation (must be set). The park row may
    name an earlier intent — recycle can swap the drain without restamping.
    """
    if not intent_id:
        return False
    if relay_state is None:
        from services.git_integration_worker.cursor_auto.job_ledger import get_ledger

        relay_state = get_ledger().read_relay_state(job_id)
    dispatch_id = str(relay_state.get("dispatch_id") or "").strip()
    if not dispatch_id:
        return False
    if park_row is None:
        park_row = load_park_row(dispatch_id=dispatch_id)
    if park_row is None or park_row.park_kind != PARK_KIND_RESTART:
        return False
    proj = park_projection(park_row)
    return proj is not None and proj.get("state") == "parked"
