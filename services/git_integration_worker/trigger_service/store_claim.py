"""Due-row expire and claim passes (predicate eval + skip-not-block)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .db import as_utc, now_iso
from .models import (
    PREDICATE_TRIGGER_TERMINAL,
    STATUS_EXPIRED,
    STATUS_FIRING,
    STATUS_SCHEDULED,
    TriggerRow,
    require_status,
    row_from_db,
)
from .predicate_eval import eval_trigger_terminal


def expire_due(
    connect_fn: Callable,
    *,
    now: datetime | None = None,
    _emit=None,
) -> list[TriggerRow]:
    """Mark scheduled rows past ``expires_at`` as ``expired`` (terminal).

    Only touches ``status='scheduled'`` rows. Expiry preempts remaining
    retries — a row with ``attempts > 0`` past ``expires_at`` becomes
    ``expired``, not ``failed``. Emits ``giw.trigger.expired`` post-commit
    for each row that actually transitioned (rowcount==1).
    """
    from services.git_integration_worker.events import publish_lib_signal

    emit = _emit or publish_lib_signal
    now_dt = as_utc(now) if now is not None else datetime.now(UTC)
    cutoff_iso = now_dt.isoformat()
    expired: list[TriggerRow] = []
    with connect_fn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id FROM triggers
            WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (STATUS_SCHEDULED, cutoff_iso),
        ).fetchall()
        for row in rows:
            trigger_id = row["id"]
            updated = conn.execute(
                """
                UPDATE triggers SET status = ?
                WHERE id = ? AND status = ?
                """,
                (require_status(STATUS_EXPIRED), trigger_id, STATUS_SCHEDULED),
            )
            if updated.rowcount == 1:
                full = conn.execute(
                    "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
                ).fetchone()
                if full is not None:
                    expired.append(row_from_db(full))
        conn.commit()
    for row in expired:
        emit(
            "giw.trigger.expired",
            {"trigger_id": row.id, "expires_at": row.expires_at},
        )
    return expired


def claim_due(
    connect_fn: Callable,
    *,
    now: datetime | None = None,
    _emit=None,
) -> TriggerRow | None:
    """Atomically claim one due scheduled row (skip-not-block for false predicates).

    Predicate-NULL rows claim as slice-1. ``trigger_terminal`` evaluates upstream
    on the same connection; false/unknown predicates skip without blocking later
    candidates. Predicate evaluation never mutates ``attempts`` or ``status``.
    Candidate SELECT is capped at 50 rows ordered by ``fire_at`` — bounds
    per-scan eval cost so a large predicate backlog cannot stall a tick.
    """
    from services.git_integration_worker.events import publish_lib_signal

    emit = _emit or publish_lib_signal
    now_dt = as_utc(now) if now is not None else datetime.now(UTC)
    cutoff_iso = now_dt.isoformat()
    claimed_at = now_iso()
    eval_failed_events: list[tuple[str, dict]] = []
    claim_event: tuple[str, dict] | None = None
    with connect_fn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute(
            """
            SELECT * FROM triggers
            WHERE status = ? AND fire_at <= ?
              AND (predicate IS NULL OR expires_at > ?)
            ORDER BY fire_at ASC
            LIMIT 50
            """,
            (STATUS_SCHEDULED, cutoff_iso, cutoff_iso),
        ).fetchall()
        result_row = None
        for candidate in candidates:
            trigger_id = candidate["id"]
            predicate = candidate["predicate"]
            if predicate is None:
                due = True
            elif predicate == PREDICATE_TRIGGER_TERMINAL:
                try:
                    due = eval_trigger_terminal(conn, candidate["predicate_args"])
                except Exception as exc:  # noqa: BLE001 — unknown ⇒ not due
                    conn.execute(
                        """
                        UPDATE triggers SET last_predicate_error = ?
                        WHERE id = ?
                        """,
                        (str(exc)[:500], trigger_id),
                    )
                    eval_failed_events.append(
                        (
                            "giw.trigger.predicate_eval_failed",
                            {
                                "trigger_id": trigger_id,
                                "error": str(exc)[:200],
                            },
                        )
                    )
                    due = False
            else:
                due = False
            if not due:
                continue
            updated = conn.execute(
                """
                UPDATE triggers
                SET status = ?, claimed_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    require_status(STATUS_FIRING),
                    claimed_at,
                    trigger_id,
                    STATUS_SCHEDULED,
                ),
            )
            if updated.rowcount == 1:
                if predicate is not None:
                    claim_event = (
                        "giw.trigger.predicate_true",
                        {
                            "trigger_id": trigger_id,
                            "predicate": predicate,
                        },
                    )
                result_row = conn.execute(
                    "SELECT * FROM triggers WHERE id = ?", (trigger_id,)
                ).fetchone()
                break
        conn.commit()
    for signal, payload in eval_failed_events:
        emit(signal, payload)
    if result_row is not None and claim_event is not None:
        emit(claim_event[0], claim_event[1])
    if result_row is not None:
        return row_from_db(result_row)
    return None
