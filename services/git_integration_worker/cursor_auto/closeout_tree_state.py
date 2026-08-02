"""Unified git-tree observation for lane-A checkpoint and deployment_state.

Both fields derive from one probe so closeout prose cannot claim ``landed`` while
the tree is still dirty on authored paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from services.git_integration_worker.cursor_auto.episode_residue import (
    residue_for_closeout,
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


def _landed_deployment_state_from_wrapper(wrapper_text: str | None) -> str | None:
    """Summarize landed-not-live propagation residue — only valid after commit."""
    if not wrapper_text:
        return None
    block = residue_for_closeout(wrapper_text)
    if block is None:
        return None
    action_lines = [
        line.lstrip("- ").strip()
        for line in block.splitlines()
        if line.strip().startswith("- ")
    ]
    count = len(action_lines)
    if count == 0:
        return None
    noun = "path" if count == 1 else "paths"
    return f"{count} landed-not-live {noun} — see RESIDUE block"


def compute_closeout_tree_state(
    *,
    source_repo: Path,
    dispatch_id: str,
    wrapper_text: str | None = None,
) -> CloseoutTreeState:
    """Derive checkpoint and deployment_state from one git-tree observation."""
    checkpoint = compute_lane_a_checkpoint_value(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
    )
    deployment_state: str | None = None
    if checkpoint.startswith("committed "):
        deployment_state = _landed_deployment_state_from_wrapper(wrapper_text)
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
    """True when deployment_state claims landed while checkpoint proves uncommitted."""
    if not deployment_state:
        return False
    if "landed-not-live" not in deployment_state:
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
