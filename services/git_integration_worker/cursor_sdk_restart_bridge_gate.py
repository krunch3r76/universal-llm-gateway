"""Defer GIW restart/drain completion while operator bridges are live (Leg F.2)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker import cursor_sdk_orphan
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_restart_deferred_live_bridge,
)
from services.git_integration_worker.cursor_sdk_orphan import active_bridge_count
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    worktree_held_by_live_bridge,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    list_registered_worktrees_with_status,
)

logger = get_logger(__name__)

_DEFER_LOG_INTERVAL_S = float(os.getenv("GIT_WORKER_BRIDGE_DEFER_LOG_INTERVAL_S", "60"))
_last_defer_emit: tuple[str | None, float] | None = None


def reset_defer_log_throttle() -> None:
    """Clear deferral log/event throttle state (tests only)."""
    global _last_defer_emit
    _last_defer_emit = None


def count_live_operator_bridges() -> int:
    """Count live **owned** bridges via registry and OS occupancy scan."""
    inproc = active_bridge_count()
    owned = cursor_sdk_orphan.owned_live_bridge_occupancy()
    held_paths: set[str] = set()
    for row in list_registered_worktrees_with_status():
        wt_raw = row["worktree_path"] if "worktree_path" in row.keys() else None
        if not wt_raw:
            continue
        wt_path = Path(str(wt_raw))
        holder = worktree_held_by_live_bridge(
            worktree_path=wt_path,
            occupancy=owned,
        )
        if holder is not None:
            held_paths.add(str(wt_path.resolve()))
    return max(inproc, len(owned), len(held_paths))


def live_bridge_blocks_restart(*, force: bool) -> bool:
    """Return True when a restart/drain-complete must defer to protect live bridges."""
    live = count_live_operator_bridges()
    if live <= 0:
        return False
    if force:
        return True
    return live > 0


def _should_emit_defer_log(*, intent_id: str | None) -> bool:
    """Throttle identical deferral logs/events to occasional emissions."""
    global _last_defer_emit
    now = time.monotonic()
    last = _last_defer_emit
    if last is not None:
        last_intent, last_at = last
        if last_intent == intent_id and now - last_at < _DEFER_LOG_INTERVAL_S:
            return False
    _last_defer_emit = (intent_id, now)
    return True


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
    if _should_emit_defer_log(intent_id=intent_id):
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
