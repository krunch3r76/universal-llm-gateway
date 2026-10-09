"""Source-sync deploy stamp — checkout HEAD label written at rsync deploy time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from deploy_identity.code_version import read_checkout_head
from deploy_identity.tree_state import resolve_tree_state

STAMP_BASENAME = ".source_sync_stamp"


def format_source_sync_stamp(
    sha: str,
    *,
    synced_at: str | None = None,
    working_tree_state: str = "clean",
) -> str:
    """Return stamp file body (line 2 is the SHA ``resolve_code_version`` reads)."""
    ts = synced_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    normalized = sha.strip().lower()
    return (
        f"{ts}\n{normalized}\n"
        "source_basis=working_tree\n"
        "code_version_semantics=checkout_head_at_source_sync\n"
        f"working_tree_state={working_tree_state}\n"
    )


@dataclass(frozen=True, slots=True)
class DeployStampPlan:
    """Stamp payload ready to copy onto a relay host after rsync."""

    content: str
    checkout_head: str


def plan_source_sync_stamp(workspace_root: Path) -> DeployStampPlan | None:
    """Plan a deploy stamp when the synced tree is clean with a readable HEAD.

    Returns ``None`` when porcelain is non-empty (dirty) or git HEAD is unavailable.
    """
    if resolve_tree_state(workspace_root) != "clean":
        return None
    sha = read_checkout_head(workspace_root)
    if not sha:
        return None
    return DeployStampPlan(
        content=format_source_sync_stamp(sha),
        checkout_head=sha.lower(),
    )


__all__ = [
    "STAMP_BASENAME",
    "DeployStampPlan",
    "format_source_sync_stamp",
    "plan_source_sync_stamp",
]
