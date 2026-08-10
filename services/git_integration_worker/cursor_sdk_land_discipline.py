"""Lane-B land incompleteness — harvest incomplete until land or discard.

Standing bind (todo:lane-b-land-discipline-harvest / agent-bus:7064 G2): a
Lane-B closeout with progress on the salvage branch (``commits_ahead ≥ 1``)
and ``landed=false`` must not project ``status: complete``. Explicit
discard/disposition or FF/content-land onto master clears the gate.
"""

from __future__ import annotations

from implement_admission.spec import CloseoutStatus

LANE_B_UNLANDED_DEVIATION = "land:lane_b_unlanded"


def apply_lane_b_land_incompleteness(
    status: CloseoutStatus,
    *,
    lane: str | None,
    landed: bool | None,
    commits_ahead: int | None,
    deviations: list[str] | None,
) -> tuple[CloseoutStatus, list[str] | None]:
    """Downgrade complete→partial when Lane-B work remains off local master."""
    if (lane or "").upper() != "B":
        return status, deviations
    if landed is not False:
        return status, deviations
    if commits_ahead is None or commits_ahead < 1:
        return status, deviations
    out_dev = list(deviations or [])
    if LANE_B_UNLANDED_DEVIATION not in out_dev:
        out_dev.append(LANE_B_UNLANDED_DEVIATION)
    if status == CloseoutStatus.COMPLETE:
        status = CloseoutStatus.PARTIAL
    return status, out_dev
