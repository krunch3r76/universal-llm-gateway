"""Lane-B land incompleteness — harvest incomplete until land or discard.

Standing bind (todo:lane-b-land-discipline-harvest / agent-bus:7064 G2): a
Lane-B closeout with progress on the salvage branch (``commits_ahead ≥ 1``)
and ``landed=false`` must not project ``status: complete``. Explicit
discard/disposition, declared ``unlanded <tip>`` when hub land is scoped out,
or FF/content-land onto master clears the gate.
"""

from __future__ import annotations

from pathlib import Path

from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    parse_land_disposition,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    commit_reachable_on_branch_ref,
    packet_hub_land_scoped_out,
)

LANE_B_UNLANDED_DEVIATION = "land:lane_b_unlanded"


def apply_lane_b_land_incompleteness(
    status: CloseoutStatus,
    *,
    lane: str | None,
    landed: bool | None,
    commits_ahead: int | None,
    deviations: list[str] | None,
    closeout_text: str | None = None,
    packet_text: str | None = None,
    branch_name: str | None = None,
    hub_repo: Path | None = None,
) -> tuple[CloseoutStatus, list[str] | None]:
    """Downgrade complete→partial when Lane-B work remains off local master."""
    if (lane or "").upper() != "B":
        return status, deviations
    if landed is not False:
        return status, deviations
    if commits_ahead is None or commits_ahead < 1:
        return status, deviations

    verb, _reason, unlanded_sha = parse_land_disposition(closeout_text)
    if (
        verb == "unlanded"
        and unlanded_sha
        and packet_hub_land_scoped_out(packet_text)
        and branch_name
        and hub_repo is not None
        and commit_reachable_on_branch_ref(
            hub_repo, branch_name=branch_name, sha=unlanded_sha
        )
    ):
        return status, deviations

    out_dev = list(deviations or [])
    if LANE_B_UNLANDED_DEVIATION not in out_dev:
        out_dev.append(LANE_B_UNLANDED_DEVIATION)
    if status == CloseoutStatus.COMPLETE:
        status = CloseoutStatus.PARTIAL
    return status, out_dev
