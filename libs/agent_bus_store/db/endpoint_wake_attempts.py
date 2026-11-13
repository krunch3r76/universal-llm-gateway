"""Shadow telemetry and S1 helpers for endpoint wake ledger rows (phase-0 observe-only)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS = 12
DISPLAY_COMPOSER_DEFER_MAX_WALL_S = 2 * 3600
_COMPOSER_DEFER_BACKOFF_CAP_S = 300
COMPOSER_DEFER_DROP_NOTICED = "composer_defer:drop_noticed"
_ESCALATION_SUBJECT = "NOTICE|endpoint wake composer defer exhausted"
_VALID_CHANNELS = frozenset(
    {"glass_queue", "glass_submit", "dom_paste", "relay_pointer"}
)
_MISSED_CAP = 5


def display_composer_defer_retry_delay_s(attempt: int) -> int:
    """Backoff schedule 30/60/120s then 300s cap (attempt 4+)."""
    steps = (30, 60, 120, _COMPOSER_DEFER_BACKOFF_CAP_S)
    n = max(1, int(attempt))
    if n > len(steps):
        return steps[-1]
    return steps[n - 1]


def _parse_stamp(stamp: str) -> datetime:
    text = stamp.strip()
    if "T" in text:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _shift(stamp: str, seconds: int) -> str:
    return (_parse_stamp(stamp) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def ledger_cols(conn: sqlite3.Connection) -> set[str]:
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(endpoint_wake_ledger)")}


def wake_detail_from_display_body(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract attempt telemetry keys echoed by display-chat-wake."""
    if not isinstance(body, dict):
        return None
    detail: dict[str, Any] = {}
    for key in (
        "channel",
        "generating",
        "composer_draft",
        "run_state",
        "mid_stream",
        "release_claim_for_retry",
        "composer_defer_reason",
    ):
        if key in body:
            detail[key] = body[key]
    if "wake_detail" in body and isinstance(body["wake_detail"], dict):
        detail.update(body["wake_detail"])
    return detail or None


def gate_fields_from_wake_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    d = dict(detail or {})
    out: dict[str, Any] = {}
    channel = str(d.get("channel") or "").strip()
    if channel in _VALID_CHANNELS:
        out["channel"] = channel
    if d.get("generating") is not None:
        out["gate_generating"] = 1 if bool(d.get("generating")) else 0
    if "composer_draft" in d:
        out["gate_draft"] = 1 if str(d.get("composer_draft") or "").strip() else 0
    if d.get("run_state") is not None:
        out["gate_run_state"] = d.get("run_state")
    if d.get("mid_stream") is not None:
        out["mid_stream"] = 1 if bool(d.get("mid_stream")) else 0
    return out


def record_attempt_rows(
    conn: sqlite3.Connection,
    *,
    row_ids: list[int],
    attempt_no: int,
    outcome: str,
    reason_code: str | None,
    started_at: str,
    finished_at: str,
    wake_detail: dict[str, Any] | None,
) -> None:
    if not _table_exists(conn, "endpoint_wake_attempt") or not row_ids:
        return
    gates = gate_fields_from_wake_detail(wake_detail)
    for rid in row_ids:
        conn.execute(
            """
            INSERT OR IGNORE INTO endpoint_wake_attempt (
                wake_row_id, attempt_no, channel, gate_generating, gate_draft,
                gate_run_state, mid_stream, outcome, reason_code, started_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(rid),
                int(attempt_no),
                gates.get("channel"),
                gates.get("gate_generating"),
                gates.get("gate_draft"),
                gates.get("gate_run_state"),
                gates.get("mid_stream"),
                str(outcome),
                reason_code,
                started_at,
                finished_at,
            ),
        )


def patch_ledger_timestamps(
    conn: sqlite3.Connection,
    *,
    row_ids: list[int],
    outcome: str,
    reason: str | None,
    stamp: str,
    cols: set[str],
) -> None:
    if not row_ids:
        return
    sets: list[str] = []
    params: list[Any] = []
    reason_s = str(reason or "").strip()
    if outcome == "delivered" and "delivered_at" in cols:
        sets.append("delivered_at=?")
        params.append(stamp)
    if reason_s and outcome in {"refused", "unknown", "pending", "skipped"}:
        if "last_failure_reason" in cols:
            sets.append("last_failure_reason=?")
            params.append(reason_s)
    if not sets:
        return
    placeholders = ",".join("?" * len(row_ids))
    conn.execute(
        f"UPDATE endpoint_wake_ledger SET {', '.join(sets)} "
        f"WHERE id IN ({placeholders})",
        (*params, *row_ids),
    )


def composer_defer_over_cap(row: sqlite3.Row | dict[str, Any], *, stamp: str) -> bool:
    raw_attempt = (
        row.get("wake_attempt_count")
        if isinstance(row, dict)
        else row["wake_attempt_count"]
    )
    attempt = int(raw_attempt or 0)
    if attempt >= DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS:
        return True
    enqueued = row.get("enqueued_at") if isinstance(row, dict) else row["enqueued_at"]
    if enqueued is None:
        enqueued = row.get("created_at") if isinstance(row, dict) else row["created_at"]
    if not enqueued:
        return False
    try:
        age_s = (_parse_stamp(stamp) - _parse_stamp(str(enqueued))).total_seconds()
    except ValueError:
        return False
    return age_s >= DISPLAY_COMPOSER_DEFER_MAX_WALL_S


def drop_notice_body(row: dict[str, Any]) -> str:
    return (
        f"wake_row_id={row.get('id')} thread={row.get('thread_id')} "
        f"turn_number={row.get('turn_number')} agent={row.get('agent')} "
        f"reason={row.get('reason')} attempts={row.get('wake_attempt_count')}"
    )


def terminal_drop_composer_defer_row(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    stamp: str,
    store: Any | None = None,
    pending_notices: list[dict[str, Any]] | None = None,
) -> None:
    rid = int(row["id"])
    if str(row.get("decided_by_rule") or "") == COMPOSER_DEFER_DROP_NOTICED:
        return
    cols = ledger_cols(conn)
    sets = [
        "state='refused'",
        "reason=?",
        "finished_at=?",
        "decided_by_rule=?",
        "wake_id=NULL",
        "claimed_at=NULL",
    ]
    params: list[Any] = [
        row.get("reason") or "composer_not_empty",
        stamp,
        COMPOSER_DEFER_DROP_NOTICED,
    ]
    if "dropped_at" in cols:
        sets.append("dropped_at=?")
        params.append(stamp)
    if "drop_reason" in cols:
        sets.append("drop_reason=?")
        params.append("dropped_cap")
    if "wake_next_attempt_at" in cols:
        sets.append("wake_next_attempt_at=?")
        params.append(None)
    conn.execute(
        f"UPDATE endpoint_wake_ledger SET {', '.join(sets)} WHERE id=?",
        (*params, rid),
    )
    notice = {
        "thread": str(row.get("thread_id")),
        "from_agent": "agent_bus",
        "to_agent": "maestro",
        "subject": _ESCALATION_SUBJECT,
        "body": drop_notice_body(row),
        "wake_row_id": rid,
    }
    if pending_notices is not None:
        pending_notices.append(notice)
    else:
        stamp_fn = getattr(store, "create_turn", None) if store is not None else None
        if stamp_fn is not None:
            try:
                stamp_fn(**{k: v for k, v in notice.items() if k != "wake_row_id"})
            except Exception:
                logger.exception("composer_defer drop notice failed row=%s", rid)
    try:
        from bus_watch.events import emit_endpoint_wake_dropped

        emit_endpoint_wake_dropped(
            wake_row_id=rid,
            thread_id=str(row.get("thread_id")),
            turn_number=int(row.get("turn_number") or 0),
            drop_reason="dropped_cap",
            attempt_count=int(row.get("wake_attempt_count") or 0),
        )
    except Exception:
        pass


def schedule_composer_defer_retry(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    stamp: str,
) -> int:
    cols = ledger_cols(conn)
    updated = 0
    for row in rows:
        rid = int(row["id"])
        prior = int(row.get("wake_attempt_count") or 0)
        next_attempt = prior + 1
        retry_at = _shift(stamp, display_composer_defer_retry_delay_s(next_attempt))
        sets = [
            "state='pending'",
            "decided_by_rule=?",
            "wake_id=NULL",
            "claimed_at=NULL",
            "finished_at=NULL",
        ]
        params: list[Any] = [None]
        if "wake_attempt_count" in cols:
            sets.append("wake_attempt_count=?")
            params.append(next_attempt)
        if "wake_last_attempt_at" in cols:
            sets.append("wake_last_attempt_at=?")
            params.append(stamp)
        if "wake_next_attempt_at" in cols:
            sets.append("wake_next_attempt_at=?")
            params.append(retry_at)
        conn.execute(
            f"UPDATE endpoint_wake_ledger SET {', '.join(sets)} WHERE id=?",
            (*params, rid),
        )
        updated += 1
        try:
            from bus_watch.events import emit_endpoint_wake_retry_scheduled

            emit_endpoint_wake_retry_scheduled(
                wake_row_id=rid,
                thread_id=str(row.get("thread_id")),
                turn_number=int(row.get("turn_number") or 0),
                wake_next_attempt_at=retry_at,
                attempt_count=next_attempt,
            )
        except Exception:
            pass
    return updated


def _missed_row_ids(
    conn: sqlite3.Connection,
    *,
    subscription_id: int,
    stamp: str,
) -> list[int]:
    cols = ledger_cols(conn)
    if "surfaced_at" not in cols or "subscription_id" not in cols:
        return []
    rows = conn.execute(
        """
        SELECT id
        FROM endpoint_wake_ledger
        WHERE subscription_id=? AND surfaced_at IS NULL
          AND (
            (drop_reason IS NOT NULL AND dropped_at IS NOT NULL)
            OR (
              state='pending' AND expected_delivery_at IS NOT NULL
              AND datetime(expected_delivery_at) < datetime(?)
            )
          )
        ORDER BY id DESC
        LIMIT ?
        """,
        (subscription_id, stamp, _MISSED_CAP + 20),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def preview_missed_prefix(
    conn: sqlite3.Connection,
    *,
    subscription_id: int | None,
    stamp: str,
) -> tuple[str, list[int]]:
    if subscription_id is None:
        return "", []
    ids = _missed_row_ids(conn, subscription_id=int(subscription_id), stamp=stamp)
    if not ids:
        return "", []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT thread_id, turn_number, drop_reason, last_failure_reason, wake_attempt_count
        FROM endpoint_wake_ledger WHERE id IN ({placeholders}) ORDER BY id DESC
        """,
        tuple(ids),
    ).fetchall()
    shown = rows[:_MISSED_CAP]
    extra = max(0, len(rows) - _MISSED_CAP)
    lines = [
        f"missed: {r['thread_id']}#{r['turn_number']}"
        f"({r['drop_reason'] or r['last_failure_reason'] or 'overdue'},"
        f"{int(r['wake_attempt_count'] or 0)})"
        for r in shown
    ]
    prefix = "\n".join(lines)
    if extra:
        prefix += f"\nmissed: +{extra} more"
    return prefix, ids


def stamp_missed_surfaced(
    conn: sqlite3.Connection,
    *,
    subscription_id: int | None,
    row_ids: list[int],
    stamp: str,
) -> None:
    if subscription_id is None or not row_ids:
        return
    cols = ledger_cols(conn)
    if "surfaced_at" not in cols:
        return
    placeholders = ",".join("?" * len(row_ids))
    conn.execute(
        f"UPDATE endpoint_wake_ledger SET surfaced_at=? WHERE id IN ({placeholders})",
        (stamp, *row_ids),
    )
    try:
        from bus_watch.events import emit_endpoint_wake_overdue_surfaced

        emit_endpoint_wake_overdue_surfaced(
            subscription_id=int(subscription_id),
            surfaced_count=len(row_ids),
        )
    except Exception:
        pass


def merge_missed_into_paste(paste_text: str, prefix: str) -> str:
    if not prefix.strip():
        return paste_text
    base = (paste_text or "").strip()
    if base:
        return f"{prefix}\n\n{base}"
    return prefix


def expected_delivery_at_for_enqueue(
    agent: str,
    thread_id: str,
    enqueued_at: str,
) -> str:
    from agent_bus_store.db.endpoint_wake_ledger import endpoint_wake_debounce_s

    window_s = int(endpoint_wake_debounce_s(agent, thread_id))
    return _shift(enqueued_at, window_s)
