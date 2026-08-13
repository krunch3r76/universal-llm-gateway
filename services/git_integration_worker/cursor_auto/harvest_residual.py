"""CONTINUITY_HARVEST_RESIDUAL payload — hop cutover accounting for Auto commissions.

Callers are hop cadence, the HTTP hop enqueue path, and the serial
``process_job`` hop branch. The payload is the successor's sense organ:
``incumbent_phase`` distinguishes lane-free from queued-unclaimed from
claimed in-flight without a second probe. Cowork ``send_later`` is not
visible to cursor-auto; ``predecessor_wake_status`` names that unknown
instead of collapsing it into silence.
"""

from __future__ import annotations

from typing import Any, Literal

from services.git_integration_worker.cursor_auto.queue import AutoJob

IncumbentPhase = Literal["none", "queued", "claimed"]
PredecessorWakeStatus = Literal["unobservable"]

_WAKE_NOTE = (
    "Predecessor Cowork send_later is unobservable to cursor-auto "
    "(predecessor_wake_status=unobservable). A one-shot armed before this "
    "hop may still fire into the retired seat; that seat must read the "
    "lane (wake-guide §7) and stand down — do not act on remembered rank."
)

_NOTE_NONE = (
    "No Auto commission on this lane at hop time "
    "(incumbent_phase=none). CDP successor still commissioned; harvest "
    "any non-Auto in-flight work from the lane tip. "
)

_NOTE_QUEUED = (
    "Unclaimed Auto commission on this lane (incumbent_phase=queued). "
    "Do not re-issue — same-thread re-issue queue_withdraws the queued "
    "predecessor before it runs. Hop did not supersede it (hop≠backtrack). "
)

_NOTE_CLAIMED = (
    "In-flight commission on this lane was preserved (hop≠backtrack). "
    "Harvest its CLOSEOUT; do not treat it as superseded. "
)


def incumbent_phase(incumbent: AutoJob | None) -> IncumbentPhase:
    """Map a hop-harvest incumbent onto none / queued / claimed for the residual."""
    if incumbent is None:
        return "none"
    if incumbent.status == "queued":
        return "queued"
    return "claimed"


def _phase_note(phase: IncumbentPhase) -> str:
    if phase == "none":
        return _NOTE_NONE + _WAKE_NOTE
    if phase == "queued":
        return _NOTE_QUEUED + _WAKE_NOTE
    return _NOTE_CLAIMED + _WAKE_NOTE


def build_harvest_residual_payload(
    job: AutoJob,
    *,
    incumbent: AutoJob | None,
    dispatch_id: str | None,
) -> dict[str, Any]:
    """Mint the residual body posted at hop cutover.

    ``incumbent_phase`` is ``none`` / ``queued`` / ``claimed``. ``queued``
    still names ``incumbent_job_id`` so a successor cannot treat a waiting
    commission as a clear lane. ``predecessor_wake_status`` is always
    ``unobservable`` — cursor-auto cannot see Cowork timers.
    """
    phase = incumbent_phase(incumbent)
    return {
        "type": "CONTINUITY_HARVEST_RESIDUAL",
        "incumbent_job_id": incumbent.job_id if incumbent else None,
        "incumbent_dispatch_id": dispatch_id,
        "incumbent_subject": incumbent.subject if incumbent else None,
        "incumbent_phase": phase,
        "predecessor_wake_status": "unobservable",
        "hop_job_id": job.job_id,
        "hop_matched_token": job.continuity_matched_token,
        "re_issue_subject": incumbent.subject if incumbent else None,
        "note": _phase_note(phase),
    }
