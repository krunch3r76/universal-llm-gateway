"""Restart-intent reads that are not the live coalescing row."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .restart_intent_states import _TERMINAL
from .restart_intent_transitions import write_status_reason

TERMINAL_LOOKBACK_S = 30 * 60


def newest_for_service(store: Any, service: str) -> Any:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM restart_intents WHERE service=? "
            "ORDER BY created_at DESC LIMIT 1",
            (service,),
        ).fetchone()
    if row is None:
        return None
    from .restart_intent_store import _row_to_intent

    return _row_to_intent(row)


def latest_terminal_for_service(
    store: Any,
    service: str,
    *,
    within_s: float = TERMINAL_LOOKBACK_S,
    now: datetime | None = None,
) -> Any:
    clock = now or datetime.now(UTC)
    placeholders = ",".join("?" * len(_TERMINAL))
    with store._connect() as conn:
        row = conn.execute(
            "SELECT * FROM restart_intents WHERE service=? "
            f"AND status IN ({placeholders}) "
            "ORDER BY COALESCE(status_changed_at, updated_at) DESC LIMIT 1",
            (service, *_TERMINAL),
        ).fetchone()
    if row is None:
        return None
    from .restart_intent_store import _row_to_intent

    intent = _row_to_intent(row)
    stamp = intent.status_changed_at or intent.updated_at
    seen = datetime.fromisoformat(stamp)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    if (clock - seen).total_seconds() > within_s:
        return None
    return intent


def note_waiting_reason(store: Any, intent_id: str, *, status_reason: str) -> bool:
    with store._connect() as conn:
        return write_status_reason(conn, intent_id, status_reason=status_reason)
