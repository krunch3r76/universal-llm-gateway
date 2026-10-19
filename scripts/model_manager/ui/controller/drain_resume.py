"""Resume routing for git-worker drain intents after manage reboot (C1 / C1b)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .restart_intent_states import STATUS_DRAINED_RESTARTING
from .restart_intent_store import Intent

__all__ = [
    "booted_after_kill_commit",
    "is_bound_drained_restarting",
    "is_drained_restarting_resume",
]


def is_drained_restarting_resume(intent: Intent) -> bool:
    return intent.status == STATUS_DRAINED_RESTARTING


def is_bound_drained_restarting(intent: Intent) -> bool:
    """Stored generation on a kill-committed row (T1 resume setup)."""
    return (
        intent.status == STATUS_DRAINED_RESTARTING
        and bool(intent.worker_id)
        and bool(intent.worker_started_at)
        and intent.drain_epoch is not None
    )


def booted_after_kill_commit(
    intent: Intent, snapshot: dict[str, Any] | None = None
) -> bool | None:
    """True when GIW started after the kill-commit transition (C1b).

    Compares ``worker_started_at`` from the live drain-state snapshot when
    present (unbound rows keep ``intent.worker_started_at`` as ``None``), else
    the stored intent field, against ``status_changed_at`` on the kill commit.
    Returns ``None`` when either timestamp is missing or unparseable.
    """
    raw_started: str | None = None
    if isinstance(snapshot, dict):
        snap_val = snapshot.get("worker_started_at")
        if isinstance(snap_val, str) and snap_val:
            raw_started = snap_val
    if raw_started is None and snapshot is None:
        raw_started = intent.worker_started_at
    if not raw_started or not intent.status_changed_at:
        return None
    try:
        worker_started = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
        status_changed = datetime.fromisoformat(
            intent.status_changed_at.replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if worker_started.tzinfo is None:
        worker_started = worker_started.replace(tzinfo=UTC)
    if status_changed.tzinfo is None:
        status_changed = status_changed.replace(tzinfo=UTC)
    return worker_started > status_changed
