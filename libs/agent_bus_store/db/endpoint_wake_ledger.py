"""Durable cursor and wake claims for the endpoint wake dispatcher."""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_seat.registry import STAGE_MANAGER_ADDRESSES, normalize_bus_address

from ..recipients import MAESTRO_WEB_ALIAS_PAIR
from .connection import connect, write_connect
from .lane_associations import get_current_lane

CONSUMER = "endpoint-wake-dispatcher"
_CLAIM_STATES = ("claimed", "unknown")
_STAGE_MANAGER_SQL = ", ".join(f"'{a}'" for a in sorted(STAGE_MANAGER_ADDRESSES))
_MAESTRO_WEB_SQL = ", ".join(f"'{a}'" for a in sorted(MAESTRO_WEB_ALIAS_PAIR))


def _mailbox_agent_sql(left: str, right: str) -> str:
    sm = _STAGE_MANAGER_SQL
    return f"({left}.agent={right}.agent OR (lower(trim({left}.agent)) IN ({sm}) AND lower(trim({right}.agent)) IN ({sm})))"


class WakeConflictError(Exception):
    """CAS or result update lost the race."""


def _now(now: str | None) -> str:
    if now:
        return now
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _shift(stamp: str, seconds: int) -> str:
    base = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def get_cursor(consumer: str = CONSUMER, *, now: str | None = None) -> dict[str, Any]:
    """Return the cursor, inserting MAX(turns.id) on first read."""
    stamp = _now(now)
    with write_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO endpoint_wake_cursor "
            "SELECT ?, COALESCE(MAX(id), 0), ? FROM turns",
            (consumer, stamp),
        )
        row = conn.execute(
            "SELECT * FROM endpoint_wake_cursor WHERE consumer=?", (consumer,)
        ).fetchone()
    assert row is not None
    return dict(row)


def advance_scan(
    consumer: str,
    expected_high_water: int,
    new_high_water: int,
    pending: list[dict[str, Any]],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """CAS the cursor and insert pending rows. 409 on a stale high water."""
    stamp = _now(now)
    with write_connect() as conn:
        row = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (consumer,),
        ).fetchone()
        current = int(row["high_water_turn_id"]) if row else None
        if current != expected_high_water:
            raise WakeConflictError("stale_high_water")
        for item in pending:
            conn.execute(
                "INSERT OR IGNORE INTO endpoint_wake_ledger "
                "(agent, thread_id, turn_id, turn_number, state, created_at) "
                "VALUES (?,?,?,?, 'pending', ?)",
                (
                    item["agent"],
                    item["thread_id"],
                    item["turn_id"],
                    item["turn_number"],
                    stamp,
                ),
            )
        conn.execute(
            "UPDATE endpoint_wake_cursor SET high_water_turn_id=?, updated_at=? "
            "WHERE consumer=?",
            (new_high_water, stamp, consumer),
        )
    return {"high_water_turn_id": new_high_water, "inserted": len(pending)}


def _same_wake_agent(left: str, right: str) -> bool:
    a = normalize_bus_address(left)
    b = normalize_bus_address(right)
    if a == b:
        return True
    if a in STAGE_MANAGER_ADDRESSES and b in STAGE_MANAGER_ADDRESSES:
        return True
    return _wake_ledger_read_cursor_agent(left) and _wake_ledger_read_cursor_agent(
        right
    )


def _references_wake_turn(
    nxt: sqlite3.Row,
    *,
    thread_id: str,
    turn_id: int,
    turn_number: int,
) -> bool:
    if nxt["supersedes_turn"] is not None and int(nxt["supersedes_turn"]) == turn_id:
        return True
    text = f"{nxt['subject'] or ''}\n{nxt['body'] or ''}"
    pattern = (
        rf"(?i)(?:(?:^|(?<![\w#])){re.escape(thread_id)}#{turn_number}\b"
        rf"|(?<![\w\d])#{turn_number}\b"
        rf"|\bturn(?:_number)?[\s:=]+#?{turn_number}\b)"
    )
    return re.search(pattern, text) is not None


def _wake_ledger_read_cursor_agent(agent: str) -> bool:
    """Alias pair uses ledger pending→delivered, not shared turns.read_at (L20)."""
    raw = agent.strip().lower()
    if raw in MAESTRO_WEB_ALIAS_PAIR:
        return True
    return normalize_bus_address(agent) in MAESTRO_WEB_ALIAS_PAIR


def skip_pending_wakes_on_pair_mark_read(
    *,
    thread_id: str,
    seat: str,
    turn_ids: list[int],
    conn: sqlite3.Connection | None = None,
    now: str | None = None,
) -> int:
    """Skip alias-pair pending ledger rows when L2 mark_read stamps turns."""
    if not turn_ids or not _wake_ledger_read_cursor_agent(seat):
        return 0
    stamp = _now(now)

    def _apply(connection: sqlite3.Connection) -> int:
        placeholders = ",".join("?" * len(turn_ids))
        cur = connection.execute(
            f"UPDATE endpoint_wake_ledger SET state='skipped', reason='already_read', "
            f"finished_at=? WHERE thread_id=? AND state='pending' "
            f"AND turn_id IN ({placeholders}) "
            f"AND lower(trim(agent)) IN ({_MAESTRO_WEB_SQL})",
            (stamp, thread_id, *turn_ids),
        )
        return int(cur.rowcount)

    if conn is not None:
        return _apply(conn)
    with write_connect() as owned:
        return _apply(owned)


def _skip_reason(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    turn = conn.execute(
        "SELECT read_at, thread FROM turns WHERE id=?", (row["turn_id"],)
    ).fetchone()
    agent = str(row["agent"])
    if (
        turn is not None
        and turn["read_at"]
        and not _wake_ledger_read_cursor_agent(agent)
    ):
        return "already_read"
    later = conn.execute(
        "SELECT from_agent, subject, body, supersedes_turn FROM turns "
        "WHERE thread=? AND id>?",
        (row["thread_id"], row["turn_id"]),
    ).fetchall()
    wake_turn_id = int(row["turn_id"])
    wake_turn_number = int(row["turn_number"])
    thread_id = str(row["thread_id"])
    for nxt in later:
        if _same_wake_agent(
            str(nxt["from_agent"] or ""), agent
        ) and _references_wake_turn(
            nxt,
            thread_id=thread_id,
            turn_id=wake_turn_id,
            turn_number=wake_turn_number,
        ):
            return "already_answered"
    return None


def _group_pending_rows(
    conn: sqlite3.Connection, group: sqlite3.Row, retry_before: str
) -> list[sqlite3.Row]:
    ga, tid = str(group["agent"]), group["thread_id"]
    filt = "AND state='pending' AND (finished_at IS NULL OR finished_at<=?)"
    if normalize_bus_address(ga) in STAGE_MANAGER_ADDRESSES:
        q = (
            f"SELECT * FROM endpoint_wake_ledger WHERE thread_id=? {filt} "
            f"AND (agent=? OR lower(trim(agent)) IN ({_STAGE_MANAGER_SQL})) "
            "ORDER BY turn_id"
        )
        return conn.execute(q, (tid, retry_before, ga)).fetchall()
    q = (
        f"SELECT * FROM endpoint_wake_ledger WHERE agent=? AND thread_id=? {filt} "
        "ORDER BY turn_id"
    )
    return conn.execute(q, (ga, tid, retry_before)).fetchall()


def resolve_subscription_for_thread(
    agent: str, thread_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Best subscribed row for ``thread_id``, including parent-thread scope."""
    parent_thread: str | None = None
    try:
        lane = get_current_lane(thread_id=thread_id)
        parent_thread = lane.get("parent_thread")
    except LookupError:
        parent_thread = None
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.* FROM agent_endpoint_subscriptions s
            WHERE s.agent = ?
              AND s.id IN (
                SELECT MAX(id) FROM agent_endpoint_subscriptions
                WHERE agent = ?
                GROUP BY thread_scope
              )
              AND s.state = 'subscribed'
              AND (
                s.thread_scope = '*'
                OR s.thread_scope = ?
                OR (? IS NOT NULL AND s.thread_scope = ?)
              )
            ORDER BY
              CASE
                WHEN s.thread_scope = ? THEN 0
                WHEN s.thread_scope = '*' THEN 2
                ELSE 1
              END,
              s.id DESC
            LIMIT 1
            """,
            (
                agent,
                agent,
                thread_id,
                parent_thread,
                parent_thread,
                thread_id,
            ),
        ).fetchall()
    if not rows:
        return None
    row = dict(rows[0])
    scope = str(row.get("thread_scope") or "")
    if scope == thread_id:
        route = "exact"
    elif scope == "*":
        route = "wildcard"
    else:
        route = "parent"
    return route, row


def claim_due_wakes(
    debounce_s: int = 600,
    claim_ttl_s: int = 300,
    limit: int = 1,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Claim one (agent, thread) group, skipping NF2 rows in the same write."""
    stamp = _now(now)
    debounce_before = _shift(stamp, -debounce_s)
    ttl_before = _shift(stamp, -claim_ttl_s)
    retry_before = _shift(stamp, -60)
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET state='unknown' "
            "WHERE state='claimed' AND claimed_at<=?",
            (ttl_before,),
        )
        group = conn.execute(
            f"""
            SELECT agent, thread_id FROM endpoint_wake_ledger p
            WHERE p.state='pending'
              AND (p.finished_at IS NULL OR p.finished_at<=?)
              AND NOT EXISTS (
                SELECT 1 FROM endpoint_wake_ledger d
                WHERE {_mailbox_agent_sql("d", "p")} AND d.thread_id=p.thread_id
                  AND d.state='delivered' AND d.finished_at>?
                  AND NOT EXISTS (
                    SELECT 1 FROM turns t
                    WHERE datetime(t.created_at) > datetime(d.finished_at)
                      AND (
                        lower(trim(t.from_agent))=lower(trim(p.agent))
                        OR (
                          lower(trim(t.from_agent)) IN ({_STAGE_MANAGER_SQL})
                          AND lower(trim(p.agent)) IN ({_STAGE_MANAGER_SQL})
                        )
                        OR (
                          d.route='cse'
                          AND lower(trim(t.from_agent))='web-anthropic'
                        )
                      )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM turns tr
                    WHERE tr.id=d.turn_id AND tr.read_at IS NOT NULL
                  )
                  AND NOT (
                    EXISTS (
                      SELECT 1 FROM turns tp
                      WHERE tp.id = p.turn_id AND tp.subject LIKE 'EAR |%'
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM agent_endpoint_subscriptions s_curr
                      LEFT JOIN agent_endpoint_subscriptions s_prev
                        ON s_prev.id = d.subscription_id
                      WHERE s_curr.agent = p.agent
                        AND s_curr.state = 'subscribed'
                        AND (
                          s_curr.thread_scope = p.thread_id
                          OR s_curr.thread_scope = '*'
                        )
                        AND s_curr.agent_id != ''
                        AND (
                          s_prev.agent_id IS NULL
                          OR s_curr.agent_id != s_prev.agent_id
                        )
                      LIMIT 1
                    )
                  )
              )
              AND NOT EXISTS (
                SELECT 1 FROM endpoint_wake_ledger u
                WHERE {_mailbox_agent_sql("u", "p")} AND u.thread_id=p.thread_id
                  AND u.state='unknown' AND u.claimed_at>?
              )
              AND NOT EXISTS (
                SELECT 1 FROM endpoint_wake_ledger c
                WHERE {_mailbox_agent_sql("c", "p")} AND c.thread_id=p.thread_id
                  AND c.state='claimed' AND c.claimed_at>?
              )
            GROUP BY agent, thread_id
            LIMIT ?
            """,
            (retry_before, debounce_before, debounce_before, ttl_before, limit),
        ).fetchone()
        if group is None:
            return {"wake_id": None, "rows": []}
        rows = _group_pending_rows(conn, group, retry_before)
        skipped: list[dict[str, Any]] = []
        claimable: list[sqlite3.Row] = []
        for row in rows:
            reason = _skip_reason(conn, row)
            if reason:
                conn.execute(
                    "UPDATE endpoint_wake_ledger SET state='skipped', reason=?, "
                    "finished_at=? WHERE id=?",
                    (reason, stamp, row["id"]),
                )
                item = dict(row)
                item["state"] = "skipped"
                item["reason"] = reason
                skipped.append(item)
            else:
                claimable.append(row)
        if not claimable:
            return {"wake_id": None, "rows": skipped}
        wake_id = uuid.uuid4().hex
        ids = [int(r["id"]) for r in claimable]
        conn.execute(
            f"UPDATE endpoint_wake_ledger SET state='claimed', wake_id=?, "
            f"claimed_at=? WHERE id IN ({','.join('?' * len(ids))})",
            (wake_id, stamp, *ids),
        )
        claimed = [
            dict(r)
            for r in conn.execute(
                """
                SELECT endpoint_wake_ledger.*,
                       turns.body AS body,
                       turns.from_agent AS from_agent
                FROM endpoint_wake_ledger
                LEFT JOIN turns ON turns.id = endpoint_wake_ledger.turn_id
                WHERE endpoint_wake_ledger.wake_id=?
                ORDER BY endpoint_wake_ledger.turn_id
                """,
                (wake_id,),
            ).fetchall()
        ]
    return {"wake_id": wake_id, "rows": claimed}


def record_wake_result(
    wake_id: str,
    outcome: str,
    reason: str | None,
    route: str | None,
    subscription_id: int | None,
    cse_registration_id: str | None,
    *,
    now: str | None = None,
) -> int:
    """Persist a result on claimed rows, or unknown rows folded from that claim."""
    stamp = _now(now)
    finished = None if outcome == "refused" else stamp
    with write_connect() as conn:
        rows = conn.execute(
            "SELECT id, state FROM endpoint_wake_ledger WHERE wake_id=?",
            (wake_id,),
        ).fetchall()
        eligible = [r for r in rows if r["state"] in _CLAIM_STATES]
        if not eligible:
            raise WakeConflictError("wake_not_claimed")
        ids = [int(r["id"]) for r in eligible]
        conn.execute(
            f"UPDATE endpoint_wake_ledger SET state=?, reason=?, route=?, "
            f"subscription_id=?, cse_registration_id=?, finished_at=? "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            (
                outcome,
                reason,
                route,
                subscription_id,
                cse_registration_id,
                finished,
                *ids,
            ),
        )
    return len(ids)


def fold_expired_claims(claim_ttl_s: int = 300, *, now: str | None = None) -> int:
    """Fold claimed rows older than the TTL to unknown. They are not re-fired."""
    stamp = _now(now)
    cutoff = _shift(stamp, -claim_ttl_s)
    with write_connect() as conn:
        cur = conn.execute(
            "UPDATE endpoint_wake_ledger SET state='unknown' "
            "WHERE state='claimed' AND claimed_at<=?",
            (cutoff,),
        )
        return int(cur.rowcount)


def list_turns_since(after_id: int, limit: int) -> list[dict[str, Any]]:
    """Compact turns newer than after_id, oldest first, without body."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
            "status, read_at, created_at FROM turns WHERE id>? "
            "ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
