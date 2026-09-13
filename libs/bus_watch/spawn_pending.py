"""Pending-spawn mutex and wake filters for gear-3 liaison.

``tick_spawn_on_wake`` and ``evaluate_spawn_predicate`` call this module so a
finished worker cannot hold the house forever and a closed unread lane cannot
look like new attention. The digest already lists those lanes; the predicate
must read lifecycle/status instead of treating any ``pending_spawn`` dict as live.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

_TERMINAL_LIFECYCLES = frozenset({"completed", "failed", "cancelled", "closed"})


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def row_is_terminal(row: dict[str, Any]) -> bool:
    """True when a digest lane or attention item is a finished worker.

    Uses ``status`` / ``lifecycle`` only. Subject-regex ``terminal`` is a
    CLOSEOUT/CHECKPOINT hint and can fire on a still-running seat.
    """
    if str(row.get("status") or "") == "closed":
        return True
    return str(row.get("lifecycle") or "").lower() in _TERMINAL_LIFECYCLES


def actionable_attention(attention: Any) -> list[Any]:
    """Wake items: unread live lanes. Budget estimates and closed workers are not."""
    items = attention if isinstance(attention, list) else []
    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "budget_estimate":
            continue
        if row_is_terminal(item):
            continue
        out.append(item)
    return out


def pending_spawn_terminal(
    pending: dict[str, Any] | None,
    *,
    is_terminal: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """True when there is no live successor mutex.

    Missing callback keeps the conservative default (pending ⇒ not terminal)
    so a caller that forgets the digest checker still will not double-spawn.
    """
    if not pending:
        return True
    if is_terminal is None:
        return False
    return is_terminal(pending)


def digest_pending_is_terminal(
    digest: dict[str, Any],
    *,
    now: float | None = None,
) -> Callable[[dict[str, Any]], bool]:
    """Build a checker: pending ``thread_id`` is terminal per digest lanes.

    A worker missing from the digest is still treated as live (admit race)
    unless ``spawned_at`` is older than ``policy.max_hop_minutes``. Vanished
    plus stale is the 10534 failure class when the mutex outlived the seat.
    """
    rows: dict[str, dict[str, Any]] = {}
    for bucket in (digest.get("lanes") or []), (digest.get("attention") or []):
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if isinstance(item, dict) and item.get("id") is not None:
                rows[str(item["id"])] = item
    max_age_s = float((digest.get("policy") or {}).get("max_hop_minutes") or 60) * 60.0
    ts = now if now is not None else datetime.now(UTC).timestamp()

    def is_terminal(pending: dict[str, Any]) -> bool:
        tid = str(pending.get("thread_id") or "").strip()
        if tid:
            row = rows.get(tid)
            if row is not None:
                return row_is_terminal(row)
        spawned = _parse_iso_ts(str(pending.get("spawned_at") or "") or None)
        if spawned is not None and (ts - spawned) > max_age_s:
            return True
        return False

    return is_terminal


def checkpoint_due_wake(state: dict[str, Any], checkpoint_due: bool) -> bool:
    """``checkpoint_due`` wakes once per ``last_cp_tick`` epoch, not every poll.

    Headless CHECKPOINT can fail to seal (a:33355). Without this latch the
    house mills a new Opus every time the prior successor STAYs and releases.
    """
    if not checkpoint_due:
        return False
    cp_tick = int(state.get("last_cp_tick") or 0)
    attempted = int(state.get("checkpoint_due_spawned_tick") or -1)
    return attempted != cp_tick


__all__ = [
    "actionable_attention",
    "checkpoint_due_wake",
    "digest_pending_is_terminal",
    "pending_spawn_terminal",
    "row_is_terminal",
]
