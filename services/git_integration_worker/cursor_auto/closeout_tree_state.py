"""Unified git-tree observation for lane-A checkpoint and deployment_state.

Both fields derive from one probe so closeout prose cannot claim ``landed`` while
the tree is still dirty on authored paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from services.git_integration_worker.cursor_auto.episode_residue import (
    obligation_deployment_state_from_wrapper,
)
from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    authored_paths_for_dispatch,
    compute_lane_a_checkpoint_value,
)

_DEPLOYMENT_STATE_LINE_RE = re.compile(r"(?im)^deployment_state:\s*.+$")


@dataclass(frozen=True)
class CloseoutTreeState:
    """Single git observation projected into relay-facing disposition fields."""

    checkpoint: str
    deployment_state: str | None


def compute_closeout_tree_state(
    *,
    source_repo: Path,
    dispatch_id: str,
    wrapper_text: str | None = None,
) -> CloseoutTreeState:
    """Derive checkpoint and deployment_state from one tree observation.

    Git porcelain/lane-refs remain the primary probe; wrapper cortex offgit URIs
    feed the row-19 ``authored_cortex:`` arm when the git plane is empty.
    """
    from implement_admission.closeout_helpers import cortex_files_root

    checkpoint = compute_lane_a_checkpoint_value(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        wrapper_text=wrapper_text,
        cortex_root=cortex_files_root(),
    )
    deployment_state: str | None = None
    if checkpoint.startswith("committed "):
        deployment_state = obligation_deployment_state_from_wrapper(wrapper_text)
    elif checkpoint != "nothing_authored":
        authored = authored_paths_for_dispatch(
            source_repo=source_repo,
            dispatch_id=dispatch_id,
        )
        if authored:
            count = len(authored)
            noun = "path" if count == 1 else "paths"
            deployment_state = (
                f"authored-not-committed — {count} {noun} "
                "await path-explicit commit"
            )
    return CloseoutTreeState(
        checkpoint=checkpoint,
        deployment_state=deployment_state,
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
    return not checkpoint.startswith("committed ")


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
