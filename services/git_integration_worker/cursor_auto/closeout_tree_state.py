"""Unified git-tree observation for lane-A checkpoint, deployment_state, and plane.

Both disposition fields and the always-present ``plane:`` headline derive from one
assembly-time observation so closeout prose cannot claim ``landed`` while the tree
is still dirty on authored paths, and so stranded Lane-B commits render without a
cross-field join (restores the one-probe invariant for the executor §2 cell).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
    annotate_plane_discrepancy,
    apply_landed_admit_gate,
    checkpoint_claims_committed,
    parse_capture_plane_keys,
    probe_three_planes,
    qualify_checkpoint_value,
    qualify_deployment_state,
    render_plane_headline,
)
from services.git_integration_worker.cursor_auto.episode_residue import (
    obligation_deployment_state_from_wrapper,
)
from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    authored_paths_for_dispatch,
    compute_lane_a_checkpoint_value,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

_DEPLOYMENT_STATE_LINE_RE = re.compile(r"(?im)^deployment_state:\s*.+$")

# Lane-A cursor-sdk writes cannot reach SeatWriteLedger (``lane_b_sweeper.REGISTRATION_GAPS``).
_LEDGER_REGISTRATION_UNAVAILABLE = (
    "ledger-registration-unavailable — cursor-sdk paths not in seat write ledger"
)


@dataclass(frozen=True)
class CloseoutTreeState:
    """Single git observation projected into relay-facing disposition fields."""

    checkpoint: str
    deployment_state: str | None
    plane_line: str
    plane_discrepancy: str | None = None


def compose_deployment_authorship(
    *,
    baseline: dict[str, Any] | None,
    authored: tuple[str, ...],
    ledger_registration_available: bool = True,
) -> str | None:
    """Rank-2 authorship claim for ``deployment_state`` (positive measurement).

    ``authored`` is already ``baseline-delta ∩ SeatWriteLedger`` from
    ``authored_paths_for_dispatch`` — baseline-delta alone never authors the
    label. Missing baseline → refuse. Empty ledger-proven ``authored`` on a
    registering seat → omit. Empty ``authored`` when the producing seat cannot
    populate the ledger → ``ledger-registration-unavailable``. Non-empty
    ledger-proven ``authored`` → ``authored-not-committed`` even on clean-admit
    ``codes={}`` (Rank-2 restore).
    """
    if baseline is None:
        return "attribution-unavailable — admit baseline missing"
    if not authored:
        if not ledger_registration_available:
            return _LEDGER_REGISTRATION_UNAVAILABLE
        return None
    count = len(authored)
    noun = "path" if count == 1 else "paths"
    return (
        f"authored-not-committed — {count} {noun} "
        "await path-explicit commit"
    )


def compute_closeout_tree_state(
    *,
    source_repo: Path,
    dispatch_id: str,
    wrapper_text: str | None = None,
) -> CloseoutTreeState:
    """Derive checkpoint, deployment_state, and plane headline from one probe.

    Lane-A porcelain/lane-refs remain the checkpoint authority; capture
    ``head_sha``/``branch`` key the three-plane probe (local refs, no fetch).
    Wrapper cortex offgit URIs feed the row-19 ``authored_cortex:`` arm when the
    git plane is empty. ``deployment_state`` authorship vocabulary is Rank-1
    gated via ``compose_deployment_authorship`` (baseline proof ≺ label).
    """
    from implement_admission.closeout_helpers import cortex_files_root

    checkpoint = compute_lane_a_checkpoint_value(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        wrapper_text=wrapper_text,
        cortex_root=cortex_files_root(),
    )
    deployment_state: str | None = None
    if checkpoint_claims_committed(checkpoint):
        deployment_state = obligation_deployment_state_from_wrapper(wrapper_text)
    elif checkpoint != "nothing_authored":
        baseline = CursorDispatchLedger.instance().read_wt_baseline(
            dispatch_id=dispatch_id
        )
        authored = authored_paths_for_dispatch(
            source_repo=source_repo,
            dispatch_id=dispatch_id,
        )
        deployment_state = compose_deployment_authorship(
            baseline=baseline,
            authored=authored,
            ledger_registration_available=False,
        )
    keys = parse_capture_plane_keys(wrapper_text)
    plane = probe_three_planes(
        source_repo,
        head_sha=keys.head_sha,
        branch=keys.branch,
    )
    plane = apply_landed_admit_gate(plane, commits_ahead=keys.commits_ahead or 0)
    checkpoint = qualify_checkpoint_value(checkpoint)
    deployment_state = qualify_deployment_state(deployment_state)
    plane_line = render_plane_headline(plane)
    discrepancy = annotate_plane_discrepancy(
        checkpoint=checkpoint,
        deployment_state=deployment_state,
        plane=plane,
    )
    return CloseoutTreeState(
        checkpoint=checkpoint,
        deployment_state=deployment_state,
        plane_line=plane_line,
        plane_discrepancy=discrepancy,
    )


def deployment_state_contradicts_checkpoint(
    *,
    checkpoint: str,
    deployment_state: str | None,
) -> bool:
    """True when deployment_state claims post-commit obligation while uncommitted."""
    if not deployment_state:
        return False
    # Current + legacy markers (landed-not-live retired; still detect stale prose).
    claims_post_commit_obligation = (
        "propagation-owed" in deployment_state
        or "landed-not-live" in deployment_state
    )
    if not claims_post_commit_obligation:
        return False
    return not checkpoint_claims_committed(checkpoint)


def strip_deployment_state_line(body: str) -> str:
    """Remove any executor- or relay-injected ``deployment_state:`` line."""
    if not _DEPLOYMENT_STATE_LINE_RE.search(body):
        return body
    lines = [
        line
        for line in body.splitlines()
        if not _DEPLOYMENT_STATE_LINE_RE.match(line)
    ]
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "CloseoutTreeState",
    "compute_closeout_tree_state",
    "deployment_state_contradicts_checkpoint",
    "strip_deployment_state_line",
]
