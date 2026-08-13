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

from services.git_integration_worker.cursor_auto.authorship_outcome_events import (
    CODE_REF_COMPUTE,
    OUTCOME_ATTRIBUTION_UNAVAILABLE,
    OUTCOME_AUTHORED_NOT_COMMITTED,
    OUTCOME_CHECKPOINT_COMMITTED,
    OUTCOME_NOTHING_AUTHORED,
    OUTCOME_OMIT,
    OUTCOME_VACANCY,
    emit_authorship_outcome,
)
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
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

_DEPLOYMENT_STATE_LINE_RE = re.compile(r"(?im)^deployment_state:\s*.+$")

# Greppable when ``has_paths_for_arc`` is false (failed/assembly-skipped/zero-row arcs).
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
    dispatch_id: str | None = None,
) -> str | None:
    """Rank-2 authorship claim for ``deployment_state`` (positive measurement).

    ``authored`` is already ``baseline-delta ∩ SeatWriteLedger`` from
    ``authored_paths_for_dispatch`` — baseline-delta alone never authors the
    label. Missing baseline → refuse. Empty ledger-proven ``authored`` on a
    registering seat → omit. Empty ``authored`` when the producing seat cannot
    populate the ledger → ``ledger-registration-unavailable``. Non-empty
    ledger-proven ``authored`` → ``authored-not-committed`` even on clean-admit
    ``codes={}`` (Rank-2 restore). Every branch emits
    ``frontier.sdk.closeout.authorship_outcome`` (including omit) so vacancy
    rate has an eligible denominator.
    """
    authored_count = len(authored)
    if baseline is None:
        outcome = OUTCOME_ATTRIBUTION_UNAVAILABLE
        text: str | None = "attribution-unavailable — admit baseline missing"
    elif not authored:
        if not ledger_registration_available:
            outcome = OUTCOME_VACANCY
            text = _LEDGER_REGISTRATION_UNAVAILABLE
        else:
            outcome = OUTCOME_OMIT
            text = None
    else:
        outcome = OUTCOME_AUTHORED_NOT_COMMITTED
        noun = "path" if authored_count == 1 else "paths"
        text = (
            f"authored-not-committed — {authored_count} {noun} "
            "await path-explicit commit"
        )
    emit_authorship_outcome(
        dispatch_id=dispatch_id or "",
        outcome=outcome,
        baseline_present=baseline is not None,
        ledger_registration_available=ledger_registration_available,
        authored_count=authored_count,
    )
    return text


def _emit_authorship_gate_arm(*, dispatch_id: str, outcome: str) -> None:
    """Tally a compute-level authorship arm that never reaches compose.

    Gate-skip arms (checkpoint-committed, nothing_authored) must still record
    so the vacancy denominator can distinguish \"eligible\" from \"never reached
    compose\". Baseline/ledger fields are unconsulted → false/zero; vacancy
    eligibility stays false via ``baseline_present``.
    """
    emit_authorship_outcome(
        dispatch_id=dispatch_id,
        outcome=outcome,
        baseline_present=False,
        ledger_registration_available=False,
        authored_count=0,
        code_ref=CODE_REF_COMPUTE,
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
    Every authorship arm — including committed and nothing_authored gate skips
    — records ``frontier.sdk.closeout.authorship_outcome``; render of
    ``deployment_state`` is unchanged.
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
        _emit_authorship_gate_arm(
            dispatch_id=dispatch_id,
            outcome=OUTCOME_CHECKPOINT_COMMITTED,
        )
    elif checkpoint != "nothing_authored":
        baseline = CursorDispatchLedger.instance().read_wt_baseline(
            dispatch_id=dispatch_id
        )
        authored = authored_paths_for_dispatch(
            source_repo=source_repo,
            dispatch_id=dispatch_id,
        )
        ledger_registration_available = SeatWriteLedger.instance().has_paths_for_arc(
            arc_id=dispatch_id
        )
        deployment_state = compose_deployment_authorship(
            baseline=baseline,
            authored=authored,
            ledger_registration_available=ledger_registration_available,
            dispatch_id=dispatch_id,
        )
    else:
        _emit_authorship_gate_arm(
            dispatch_id=dispatch_id,
            outcome=OUTCOME_NOTHING_AUTHORED,
        )
    keys = parse_capture_plane_keys(wrapper_text)
    plane = probe_three_planes(
        source_repo,
        head_sha=keys.head_sha,
        branch=keys.branch,
    )
    plane = apply_landed_admit_gate(
        plane,
        commits_ahead=keys.commits_ahead,
        commits_ahead_presence=keys.commits_ahead_presence,
        git_land_plane_uncomputable=keys.git_land_plane_uncomputable,
    )
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
