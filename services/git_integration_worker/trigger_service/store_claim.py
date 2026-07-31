"""Due-row expire and claim passes (predicate eval + skip-not-block)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .config import defer_threshold, fire_interval_s
from .db import as_utc, now_iso
from .fleet_idle import FleetIdleSnapshot, FleetVerdict, read_fleet_idle_memoized
from .models import (
    PREDICATE_DEFERRING,
    PREDICATE_FLEET_IDLE,
    PREDICATE_TRIGGER_TERMINAL,
    STATUS_EXPIRED,
    STATUS_FIRING,
    STATUS_SCHEDULED,
    TriggerRow,
    require_status,
    row_from_db,
)
from .pass_snapshot_publish import grace_s_from_predicate_args, publish_pass_snapshot
from .predicate_eval import eval_fleet_idle_predicate, eval_trigger_terminal


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


def _defer_row(
    conn,
    *,
    trigger_id: str,
    now_dt: datetime,
    predicate: str,
    defer_events: list[tuple[str, dict]],
    fleet_verdict: str | None = None,
) -> None:
    """Push ``fire_at`` one pass forward; row stays ``scheduled``."""
    defer_until = (now_dt + timedelta(seconds=fire_interval_s())).isoformat()
    deferred_at = now_dt.isoformat()
    row = conn.execute(
        "SELECT defer_count, degraded FROM triggers WHERE id = ?",
        (trigger_id,),
    ).fetchone()
    prior_count = int(row["defer_count"]) if row else 0
    new_count = prior_count + 1
    was_degraded = bool(row and int(row["degraded"]))
    set_degraded = 1 if new_count >= defer_threshold() else int(was_degraded)
    conn.execute(
        """
        UPDATE triggers
        SET fire_at = ?,
            defer_count = ?,
            last_deferred_at = ?,
            last_fleet_verdict = COALESCE(?, last_fleet_verdict),
            degraded = ?
        WHERE id = ? AND status = ?
        """,
        (
            defer_until,
            new_count,
            deferred_at,
            fleet_verdict,
            set_degraded,
            trigger_id,
            STATUS_SCHEDULED,
        ),
    )
    payload: dict = {
        "trigger_id": trigger_id,
        "predicate": predicate,
        "fire_at": defer_until,
        "defer_count": new_count,
        "last_deferred_at": deferred_at,
    }
    if fleet_verdict is not None:
        payload["fleet_verdict"] = fleet_verdict
    defer_events.append(("giw.trigger.predicate_deferred", payload))
    if new_count >= defer_threshold() and not was_degraded:
        defer_events.append(
            (
                "giw.trigger.defer_degraded",
                {
                    "trigger_id": trigger_id,
                    "defer_count": new_count,
                    "threshold": defer_threshold(),
                    "fleet_verdict": fleet_verdict,
                },
            )
        )


def _reset_defer_on_claim(conn, trigger_id: str) -> None:
    """Clear defer streak when predicate passes and row is claimed."""
    conn.execute(
        """
        UPDATE triggers
        SET defer_count = 0,
            last_deferred_at = NULL,
            last_fleet_verdict = NULL,
            degraded = 0
        WHERE id = ?
        """,
        (trigger_id,),
    )


def claim_due(
    connect_fn: Callable,
    *,
    now: datetime | None = None,
    _emit=None,
) -> TriggerRow | None:
    """Atomically claim one due scheduled row (skip-not-block for false predicates).

    ``trigger_terminal``: false predicate skips without blocking later candidates.
    ``fleet_idle``: false predicate defers ``fire_at`` one pass (row stays scheduled).
    Predicate evaluation never mutates ``attempts`` or ``status`` except defer bump.
    """
    from services.git_integration_worker.events import publish_lib_signal

    emit = _emit or publish_lib_signal
    now_dt = as_utc(now) if now is not None else datetime.now(UTC)
    cutoff_iso = now_dt.isoformat()
    claimed_at = now_iso()
    eval_failed_events: list[tuple[str, dict]] = []
    defer_events: list[tuple[str, dict]] = []
    claim_event: tuple[str, dict] | None = None
    fleet_snapshot: FleetIdleSnapshot | None = None
    with connect_fn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute(
            """
            SELECT * FROM triggers
            WHERE status = ? AND fire_at <= ?
              AND (
                predicate IS NULL
                OR expires_at IS NULL
                OR expires_at > ?
              )
            ORDER BY fire_at ASC
            LIMIT 50
            """,
            (STATUS_SCHEDULED, cutoff_iso, cutoff_iso),
        ).fetchall()
        result_row = None
        for candidate in candidates:
            trigger_id = candidate["id"]
            predicate = candidate["predicate"]
            fleet_verdict: str | None = None
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
            elif predicate == PREDICATE_FLEET_IDLE:
                try:
                    first_read = fleet_snapshot is None
                    if fleet_snapshot is None:
                        fleet_snapshot = read_fleet_idle_memoized()
                    if first_read:
                        publish_pass_snapshot(
                            fleet_snapshot,
                            trigger_row_id=trigger_id,
                            defer_count=int(candidate["defer_count"] or 0),
                            grace_s=grace_s_from_predicate_args(
                                candidate["predicate_args"]
                            ),
                            pass_at=now_dt,
                        )
                    fleet_verdict = fleet_snapshot.verdict.value
                    due = eval_fleet_idle_predicate(
                        candidate["predicate_args"],
                        snapshot=fleet_snapshot,
                    )
                except Exception as exc:  # noqa: BLE001
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
                    fleet_verdict = FleetVerdict.UNDETERMINED.value
            else:
                due = False
            if not due:
                if predicate in PREDICATE_DEFERRING:
                    _defer_row(
                        conn,
                        trigger_id=trigger_id,
                        now_dt=now_dt,
                        predicate=predicate,
                        defer_events=defer_events,
                        fleet_verdict=fleet_verdict,
                    )
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
                _reset_defer_on_claim(conn, trigger_id)
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
    for signal, payload in defer_events:
        emit(signal, payload)
    if result_row is not None and claim_event is not None:
        emit(claim_event[0], claim_event[1])
    if result_row is not None:
        return row_from_db(result_row)
    return None
