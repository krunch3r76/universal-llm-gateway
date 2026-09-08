"""Defer GIW restart/drain completion while operator bridges are live (Leg F.2)."""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_restart_deferred_live_bridge,
)
from services.git_integration_worker.cursor_sdk_orphan import (
    active_bridge_count,
    live_bridge_occupancy,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    worktree_held_by_live_bridge,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    list_registered_worktrees_with_status,
)

logger = get_logger(__name__)


def count_live_operator_bridges() -> int:
    """Count live bridges via in-process registry and OS occupancy scan."""
    inproc = active_bridge_count()
    occupancy = live_bridge_occupancy()
    held_paths: set[str] = set()
    for row in list_registered_worktrees_with_status():
        wt_raw = row["worktree_path"] if "worktree_path" in row.keys() else None
        if not wt_raw:
            continue
        wt_path = Path(str(wt_raw))
        holder = worktree_held_by_live_bridge(worktree_path=wt_path)
        if holder is not None:
            held_paths.add(str(wt_path.resolve()))
    return max(inproc, len(occupancy), len(held_paths))


def live_bridge_blocks_restart(*, force: bool) -> bool:
    """Return True when a restart/drain-complete must defer to protect live bridges."""
    live = count_live_operator_bridges()
    if live <= 0:
        return False
    if force:
        return True
    return live > 0


def defer_restart_for_live_bridges(
    *,
    force: bool,
    intent_id: str | None = None,
    bridge_count: int | None = None,
) -> bool:
    """Emit ``sdk.restart.deferred_live_bridge`` when ``force`` would kill bridges."""
    count = bridge_count if bridge_count is not None else count_live_operator_bridges()
    if not live_bridge_blocks_restart(force=force):
        return False
    emit_sdk_restart_deferred_live_bridge(
        bridge_count=count,
        force=force,
        intent_id=intent_id,
    )
    logger.info(
        "giw restart deferred for live bridges: count=%s force=%s intent_id=%s",
        count,
        force,
        intent_id,
    )
    return True


__all__ = [
    "count_live_operator_bridges",
    "defer_restart_for_live_bridges",
    "live_bridge_blocks_restart",
]
