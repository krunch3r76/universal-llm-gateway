"""Durable cursor and wake claims for the endpoint wake dispatcher."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_seat.registry import STAGE_MANAGER_ADDRESSES, normalize_bus_address
from cursor_bridge.cursor_window_config import (
    cdp_origin_for_gui_host,
    normalize_gui_host,
)

from ..recipients import MAESTRO_WEB_ALIAS_PAIR
from .connection import connect, write_connect
from .lane_associations import get_current_lane

# Reply subjects that count as answering a wake turn (plus turn reference or supersedes).
# Excludes PROGRESS/STATUS/CHECKPOINT and other milestone heads that may cite a turn.
_WAKE_REPLY_SUBJECT_HEADS = frozenset(
    {"ACK", "ANSWER", "REPLY", "RESPONSE", "CONSULT_REPLY"}
)
_CDP_REPLY_SUBJECT_RE = re.compile(r"^cdp reply\s*[—-]\s*", re.I)

CONSUMER = "endpoint-wake-dispatcher"
_CLAIM_STATES = ("claimed", "unknown")
_SCAN_ITEM_STATES = frozenset({"pending", "skipped"})
_CLAIM_SKIP_GROUP_CAP = 32
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


def _parse_utc_stamp(stamp: str) -> datetime:
    """Parse agent-bus stamps: space form or wire ``%Y-%m-%dT%H:%M:%SZ``."""
    text = stamp.strip()
    if "T" in text:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _shift(stamp: str, seconds: int) -> str:
    base = _parse_utc_stamp(stamp)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _ledger_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(endpoint_wake_ledger)").fetchall()
    return {str(r[1]) for r in rows}


def _encode_delivery_failure_detail(
    reason: str | None, **fields: Any
) -> str | None:
    """Stable JSON blob for agent_bus_read / advisory tooling (nullable)."""
    base = str(reason or "").strip()
    if not base and not fields:
        return None
    payload: dict[str, Any] = {"reason_code": base} if base else {}
    for key, val in fields.items():
        if val is None:
            continue
        payload[key] = val
    if len(payload) <= 1 and not base:
        return None
    try:
        return json.dumps(payload, sort_keys=True)
    except (TypeError, ValueError):
        return base or None


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
    """CAS the cursor and insert pending rows. 409 on a stale high water.

    An item with ``state='skipped'`` is a scan-time suppression tombstone: it is
    inserted terminal with its ``reason`` so the decision is visible on the ledger.
    """
    stamp = _now(now)
    for item in pending:
        if item.get("state", "pending") not in _SCAN_ITEM_STATES:
            raise ValueError(f"advance_scan: unsupported item state {item['state']!r}")
    with write_connect() as conn:
        row = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (consumer,),
        ).fetchone()
        current = int(row["high_water_turn_id"]) if row else None
        if current != expected_high_water:
            raise WakeConflictError("stale_high_water")
        filtered: list[dict[str, Any]] = []
        for item in pending:
            turn_row = conn.execute(
                "SELECT read_at FROM turns WHERE id=?", (int(item["turn_id"]),)
            ).fetchone()
            if turn_row is not None and turn_row["read_at"]:
                continue
            filtered.append(item)
        ledger_cols = _ledger_columns(conn)
        has_delivery_agent_id = "delivery_agent_id" in ledger_cols
        has_subscription_id = "subscription_id" in ledger_cols
        for item in filtered:
            state = item.get("state", "pending")
            delivery_id = item.get("delivery_agent_id")
            subscription_id = item.get("subscription_id")
            if state == "pending":
                existing = conn.execute(
                    "SELECT id, state, reason FROM endpoint_wake_ledger "
                    "WHERE agent=? AND thread_id=? AND turn_id=?",
                    (item["agent"], item["thread_id"], item["turn_id"]),
                ).fetchone()
                if (
                    existing is not None
                    and str(existing["state"] or "") == "skipped"
                    and str(existing["reason"] or "") == "no_route:scan_holder"
                ):
                    if has_subscription_id:
                        conn.execute(
                            "UPDATE endpoint_wake_ledger SET state='pending', reason=NULL, "
                            "finished_at=NULL, decided_by_rule=?, subscription_id=COALESCE(?, "
                            "subscription_id) WHERE id=?",
                            (
                                item.get("decided_by_rule"),
                                subscription_id,
                                existing["id"],
                            ),
                        )
                    else:
                        conn.execute(
                            "UPDATE endpoint_wake_ledger SET state='pending', reason=NULL, "
                            "finished_at=NULL, decided_by_rule=? WHERE id=?",
                            (item.get("decided_by_rule"), existing["id"]),
                        )
                    continue
            if has_delivery_agent_id and has_subscription_id:
                conn.execute(
                    "INSERT OR IGNORE INTO endpoint_wake_ledger "
                    "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
                    "finished_at, decided_by_rule, delivery_agent_id, subscription_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["agent"],
                        item["thread_id"],
                        item["turn_id"],
                        item["turn_number"],
                        state,
                        item.get("reason") if state == "skipped" else None,
                        stamp,
                        stamp if state == "skipped" else None,
                        item.get("decided_by_rule"),
                        delivery_id if delivery_id else None,
                        subscription_id,
                    ),
                )
            elif has_delivery_agent_id:
                conn.execute(
                    "INSERT OR IGNORE INTO endpoint_wake_ledger "
                    "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
                    "finished_at, decided_by_rule, delivery_agent_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["agent"],
                        item["thread_id"],
                        item["turn_id"],
                        item["turn_number"],
                        state,
                        item.get("reason") if state == "skipped" else None,
                        stamp,
                        stamp if state == "skipped" else None,
                        item.get("decided_by_rule"),
                        delivery_id if delivery_id else None,
                    ),
                )
            elif has_subscription_id:
                conn.execute(
                    "INSERT OR IGNORE INTO endpoint_wake_ledger "
                    "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
                    "finished_at, decided_by_rule, subscription_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["agent"],
                        item["thread_id"],
                        item["turn_id"],
                        item["turn_number"],
                        state,
                        item.get("reason") if state == "skipped" else None,
                        stamp,
                        stamp if state == "skipped" else None,
                        item.get("decided_by_rule"),
                        subscription_id,
                    ),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO endpoint_wake_ledger "
                    "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
                    "finished_at, decided_by_rule) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        item["agent"],
                        item["thread_id"],
                        item["turn_id"],
                        item["turn_number"],
                        state,
                        item.get("reason") if state == "skipped" else None,
                        stamp,
                        stamp if state == "skipped" else None,
                        item.get("decided_by_rule"),
                    ),
                )
        conn.execute(
            "UPDATE endpoint_wake_cursor SET high_water_turn_id=?, updated_at=? "
            "WHERE consumer=?",
            (new_high_water, stamp, consumer),
        )
        return {"high_water_turn_id": new_high_water, "inserted": len(filtered)}


def expire_scan_holder_tombstones(cutoff_stamp: str) -> int:
    """Terminal-expire ledger-stale ``no_route:scan_holder`` rows (no row LIMIT)."""
    with write_connect() as conn:
        stamp = _now(None)
        cur = conn.execute(
            "UPDATE endpoint_wake_ledger SET state='skipped', "
            "reason='no_route:scan_holder_expired', finished_at=? "
            "WHERE state='skipped' AND reason='no_route:scan_holder' "
            "AND created_at < ?",
            (stamp, cutoff_stamp),
        )
        return int(cur.rowcount)


def list_scan_holder_candidates(cutoff_stamp: str) -> list[dict[str, Any]]:
    """Fresh scan-holder tombstones (read-only connect; no LIMIT)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT l.id, l.agent, l.thread_id, l.turn_id, t.created_at "
            "FROM endpoint_wake_ledger l "
            "JOIN turns t ON t.id = l.turn_id "
            "WHERE l.state='skipped' AND l.reason='no_route:scan_holder' "
            "AND l.created_at >= ? "
            "ORDER BY l.id",
            (cutoff_stamp,),
        ).fetchall()
    return [dict(r) for r in rows]


def promote_scan_holder_rows(
    promote_ids: Sequence[int],
    expire_ids: Sequence[int],
) -> int:
    """CAS-promote or expire scan-holder rows in one short write ticket."""
    if not promote_ids and not expire_ids:
        return 0
    stamp = _now(None)
    promoted = 0
    with write_connect() as conn:
        if promote_ids:
            placeholders = ",".join("?" * len(promote_ids))
            cur = conn.execute(
                f"UPDATE endpoint_wake_ledger SET state='pending', reason=NULL, "
                f"finished_at=NULL WHERE id IN ({placeholders}) "
                f"AND state='skipped' AND reason='no_route:scan_holder'",
                tuple(int(i) for i in promote_ids),
            )
            promoted = int(cur.rowcount)
        if expire_ids:
            placeholders = ",".join("?" * len(expire_ids))
            conn.execute(
                f"UPDATE endpoint_wake_ledger SET state='skipped', "
                f"reason='no_route:scan_holder_expired', finished_at=? "
                f"WHERE id IN ({placeholders}) "
                f"AND state='skipped' AND reason='no_route:scan_holder'",
                (stamp, *(int(i) for i in expire_ids)),
            )
    return promoted


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


def _wake_reply_subject_head(subject: str | None) -> str:
    """Subject prefix before ``|`` (matches ``maestro_subject_head`` in send_prep)."""
    head = str(subject or "").split("|", 1)[0].strip().upper()
    if not head:
        return ""
    return head.split()[0]


def _is_wake_reply_acknowledgement(subject: str | None) -> bool:
    """True when the subject marks an explicit answer/ack, not status or progress."""
    subj = str(subject or "").strip()
    if not subj:
        return False
    if _CDP_REPLY_SUBJECT_RE.match(subj):
        return True
    return _wake_reply_subject_head(subj) in _WAKE_REPLY_SUBJECT_HEADS


def _supersedes_wake_turn(nxt: sqlite3.Row, *, turn_id: int) -> bool:
    return nxt["supersedes_turn"] is not None and int(nxt["supersedes_turn"]) == turn_id


def _references_wake_turn(
    nxt: sqlite3.Row,
    *,
    thread_id: str,
    turn_number: int,
) -> bool:
    text = f"{nxt['subject'] or ''}\n{nxt['body'] or ''}"
    pattern = (
        rf"(?i)(?:(?:^|(?<![\w#])){re.escape(thread_id)}#{turn_number}\b"
        rf"|(?<![\w\d])#{turn_number}\b"
        rf"|\bturn(?:_number)?[\s:=]+#?{turn_number}\b)"
    )
    return re.search(pattern, text) is not None


def _wake_ledger_read_cursor_agent(agent: str) -> bool:
    """True for maestro/web-anthropic alias pair (mark-read skip path only)."""
    raw = agent.strip().lower()
    if raw in MAESTRO_WEB_ALIAS_PAIR:
        return True
    return normalize_bus_address(agent) in MAESTRO_WEB_ALIAS_PAIR


def canonical_wake_recipient(agent: str) -> str:
    """Single dedupe bucket for maestro ↔ web-anthropic continuity mailboxes."""
    normalized = normalize_bus_address(str(agent or "").strip())
    if normalized in MAESTRO_WEB_ALIAS_PAIR:
        return "maestro"
    return normalized


def _agent_matches_canonical(agent: str, canonical: str) -> bool:
    return canonical_wake_recipient(agent) == canonical_wake_recipient(canonical)


def _ledger_agents_for_canonical(canonical: str) -> tuple[str, ...]:
    if canonical_wake_recipient(canonical) == "maestro":
        return tuple(sorted(MAESTRO_WEB_ALIAS_PAIR))
    return (canonical_wake_recipient(canonical),)


def endpoint_wake_covers_turn(
    conn: sqlite3.Connection,
    *,
    agent: str,
    thread_id: str,
    turn_number: int,
    claim_ttl_s: int = 300,
) -> bool:
    """True when endpoint wake already delivered or holds a live claim for this turn."""
    canonical = canonical_wake_recipient(agent)
    agents = _ledger_agents_for_canonical(canonical)
    if not agents:
        return False
    placeholders = ",".join("?" * len(agents))
    stamp = _now(None)
    ttl_before = _shift(stamp, -claim_ttl_s)
    rows = conn.execute(
        f"SELECT agent, state, claimed_at FROM endpoint_wake_ledger "
        f"WHERE thread_id=? AND turn_number=? "
        f"AND lower(trim(agent)) IN ({placeholders})",
        (thread_id, int(turn_number), *[a.lower() for a in agents]),
    ).fetchall()
    for row in rows:
        if not _agent_matches_canonical(str(row["agent"]), agent):
            continue
        state = str(row["state"])
        if state == "delivered":
            return True
        if state == "claimed":
            claimed_at = str(row["claimed_at"] or "")
            if claimed_at and claimed_at > ttl_before:
                return True
    return False


def suppress_endpoint_wake_for_turn(
    *,
    agent: str,
    thread_id: str,
    turn_number: int,
    reason: str = "deduped_owed_wake",
    now: str | None = None,
) -> int:
    """Skip pending rows and stamp a skipped tombstone for cross-path dedupe."""
    stamp = _now(now)
    canonical = canonical_wake_recipient(agent)
    agents = _ledger_agents_for_canonical(canonical)
    placeholders = ",".join("?" * len(agents))
    with write_connect() as conn:
        cur = conn.execute(
            f"UPDATE endpoint_wake_ledger SET state='skipped', reason=?, finished_at=? "
            f"WHERE thread_id=? AND turn_number=? AND state='pending' "
            f"AND lower(trim(agent)) IN ({placeholders})",
            (reason, stamp, thread_id, int(turn_number), *agents),
        )
        updated = int(cur.rowcount)
        turn = conn.execute(
            "SELECT id FROM turns WHERE thread=? AND turn_number=? LIMIT 1",
            (thread_id, int(turn_number)),
        ).fetchone()
        turn_id = int(turn["id"]) if turn else 0
        if turn_id:
            conn.execute(
                "INSERT OR IGNORE INTO endpoint_wake_ledger "
                "(agent, thread_id, turn_id, turn_number, state, reason, created_at, finished_at) "
                "VALUES (?,?,?,?, 'skipped', ?, ?, ?)",
                (canonical, thread_id, turn_id, int(turn_number), reason, stamp, stamp),
            )
        return updated


def _owed_wake_covers_turn(*, agent: str, thread_id: str, turn_number: int) -> bool:
    """True when owed_wake successfully delivered for this turn (maestro/web alias pair)."""
    if normalize_bus_address(agent) not in MAESTRO_WEB_ALIAS_PAIR:
        return False
    try:
        from scripts.model_manager.ui.controller.owed_wake_ledger import (
            decode_turn_refs,
        )
        from scripts.model_manager.ui.controller.restart_intent_store import (
            RestartIntentStore,
        )
    except ImportError:
        return False
    store = RestartIntentStore.instance()
    lane = str(thread_id)
    want_n = int(turn_number)
    try:
        with store._connect() as conn:
            rows = conn.execute(
                """
                SELECT turn_refs FROM owed_wake
                WHERE dispatched_at IS NOT NULL
                  AND COALESCE(dispatch_outcome, 'dispatched') = 'dispatched'
                """
            ).fetchall()
    except Exception:
        return False
    for row in rows:
        for ref in decode_turn_refs(row["turn_refs"]):
            if str(ref.get("thread_id") or "") != lane:
                continue
            try:
                ref_n = int(ref.get("turn_number"))
            except (TypeError, ValueError):
                continue
            if ref_n == want_n:
                return True
    return False


def _seat_superseded_skip_reason(
    conn: sqlite3.Connection, *, agent: str, thread_id: str
) -> str | None:
    """Skip when the resolved subscription route's CSE seat is superseded.

    Tombstone subscriptions (state != subscribed) suppress wakes only for
    surface ``cse``. Other surfaces (grok, display, …) may be retired without
    blocking synthetic routes (e.g. Orion chat) that do not use that row.
    """
    _ = conn
    resolved = resolve_subscription_for_thread(agent, thread_id)
    if resolved is None:
        with connect() as sub_conn:
            row = sub_conn.execute(
                """
                SELECT state, surface FROM agent_endpoint_subscriptions
                WHERE agent = ?
                  AND id IN (
                    SELECT MAX(id) FROM agent_endpoint_subscriptions
                    WHERE agent = ?
                    GROUP BY thread_scope
                  )
                  AND (thread_scope = '*' OR thread_scope = ?)
                ORDER BY CASE WHEN thread_scope = ? THEN 0
                              WHEN thread_scope = '*' THEN 2 ELSE 1 END,
                         id DESC
                LIMIT 1
                """,
                (agent, agent, thread_id, thread_id),
            ).fetchone()
        if row is not None and str(row["state"] or "") != "subscribed":
            if str(row["surface"] or "") == "cse":
                return "seat_superseded"
        return None
    _route, sub = resolved
    if str(sub.get("surface") or "") != "cse":
        return None
    agent_id = str(sub.get("agent_id") or thread_id).strip()
    if not agent_id:
        return None
    try:
        import re

        from services.git_integration_worker.cse_session_holders import (
            _ledger_conn,
            ensure_schema,
            get_driving_holder_for_lane,
            resolve_holder_by_registration,
        )

        with _ledger_conn() as hconn:
            ensure_schema(hconn)
            if re.fullmatch(r"^\d{1,10}$", agent_id):
                holder = get_driving_holder_for_lane(hconn, agent_id)
            else:
                holder = resolve_holder_by_registration(hconn, agent_id)
    except Exception:
        return None
    if holder is None:
        return None
    if str(holder.get("seat_state") or "") == "superseded":
        return "seat_superseded"
    return None


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
    thread_id = str(row["thread_id"])
    turn_number = int(row["turn_number"])
    if turn is not None and turn["read_at"]:
        return "already_read"
    if _owed_wake_covers_turn(
        agent=agent, thread_id=thread_id, turn_number=turn_number
    ):
        return "deduped_owed_wake"
    superseded = _seat_superseded_skip_reason(conn, agent=agent, thread_id=thread_id)
    if superseded:
        return superseded
    later = conn.execute(
        "SELECT from_agent, subject, body, supersedes_turn FROM turns "
        "WHERE thread=? AND id>?",
        (row["thread_id"], row["turn_id"]),
    ).fetchall()
    wake_turn_id = int(row["turn_id"])
    wake_turn_number = turn_number
    for nxt in later:
        if not _same_wake_agent(str(nxt["from_agent"] or ""), agent):
            continue
        if _supersedes_wake_turn(nxt, turn_id=wake_turn_id):
            return "already_answered"
        if _is_wake_reply_acknowledgement(
            str(nxt["subject"] or "")
        ) and _references_wake_turn(
            nxt,
            thread_id=thread_id,
            turn_number=wake_turn_number,
        ):
            return "already_answered"
    return None


def _pending_retry_ready_sql(alias: str = "") -> str:
    """Orion ``deferred_streaming`` uses ``finished_at`` as next-attempt time (wall clock)."""
    prefix = f"{alias}." if alias else ""
    return f"""(
      {prefix}finished_at IS NULL
      OR ({prefix}reason='deferred_streaming' AND {prefix}finished_at<=?)
      OR (COALESCE({prefix}reason,'')!='deferred_streaming' AND {prefix}finished_at<=?)
    )"""


def _orion_composer_defer_claim_ready_sql(alias: str, *, in_sql: str) -> str:
    """Orion bounded-retry pending rows wait until ``wake_next_attempt_at`` (026+)."""
    ap = f"{alias}." if alias else ""
    return f"""(
      lower(trim({ap}agent)) != 'orion'
      OR {ap}reason NOT IN {in_sql}
      OR {ap}wake_next_attempt_at IS NULL
      OR datetime({ap}wake_next_attempt_at) <= datetime(?)
    )"""


def _group_pending_rows(
    conn: sqlite3.Connection,
    group: sqlite3.Row,
    *,
    stamp: str,
    retry_before: str,
    orion_next_gate_sql: str = "",
    orion_next_extra_params: tuple[Any, ...] = (),
) -> list[sqlite3.Row]:
    """All pending ledger rows for the (agent, thread_id) group eligible to claim together."""
    ga, tid = str(group["agent"]), group["thread_id"]
    filt = f"AND state='pending' AND {_pending_retry_ready_sql()}{orion_next_gate_sql}"
    if normalize_bus_address(ga) in STAGE_MANAGER_ADDRESSES:
        q = (
            f"SELECT * FROM endpoint_wake_ledger WHERE thread_id=? {filt} "
            f"AND (agent=? OR lower(trim(agent)) IN ({_STAGE_MANAGER_SQL})) "
            "ORDER BY turn_id"
        )
        params = (tid, stamp, retry_before, *orion_next_extra_params, ga)
        return conn.execute(q, params).fetchall()
    q = (
        f"SELECT * FROM endpoint_wake_ledger WHERE agent=? AND thread_id=? {filt} "
        "ORDER BY turn_id"
    )
    params2 = (ga, tid, stamp, retry_before, *orion_next_extra_params)
    return conn.execute(q, params2).fetchall()


def resolve_subscription_for_thread(
    agent: str,
    thread_id: str,
    *,
    agent_id: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Best subscribed row for ``thread_id``, including parent-thread scope."""
    parent_thread: str | None = None
    try:
        lane = get_current_lane(thread_id=thread_id)
        parent_thread = lane.get("parent_thread")
    except LookupError:
        parent_thread = None
    aid_filter = str(agent_id or "").strip() or None
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.* FROM agent_endpoint_subscriptions s
            WHERE s.agent = ?
              AND s.actuation IS NULL
              AND s.id IN (
                SELECT MAX(id) FROM agent_endpoint_subscriptions
                WHERE agent = ? AND actuation IS NULL
                GROUP BY thread_scope
              )
              AND s.state = 'subscribed'
              AND (
                s.thread_scope = '*'
                OR s.thread_scope = ?
                OR (? IS NOT NULL AND s.thread_scope = ?)
              )
              AND (? IS NULL OR s.agent_id = ?)
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
                aid_filter,
                aid_filter,
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


_WAKE_DELIVERY_AGENT = "cursor"
_DELIVERY_BINDING_SOURCE = "agent_bus_store.delivery_bindings_resolve"
_ORION_WAKE_HOUSE = "21207"
ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT = 600
ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT = 30
ENDPOINT_WAKE_TAB_COALESCE_S_DEFAULT = 3.0
ENDPOINT_WAKE_TAB_COALESCE_MAX_S_DEFAULT = 15.0
ORION_DEFERRED_STREAMING_MAX_ATTEMPTS = 25
ORION_COMPOSER_DEFER_MAX_ATTEMPTS = 25
ORION_DEFERRED_RETRY_BASE_S = 30
ORION_DEFERRED_RETRY_MAX_S = 300
_ORION_DEFERRED_ATTEMPT_PREFIX = "orion_deferred_attempt:"
_ORION_COMPOSER_DEFER_ATTEMPT_PREFIX = "orion_composer_defer_attempt:"


def _is_orion_chat_wake_group(agent: str, thread_id: str) -> bool:
    """True for pinned-house Orion chat wakes (actuation type orion_chat)."""
    if str(thread_id) != _ORION_WAKE_HOUSE:
        return False
    return normalize_bus_address(agent) == "orion"


def endpoint_wake_debounce_s(agent: str, thread_id: str) -> int:
    """Post-delivery debounce and pending coalesce window per endpoint kind."""
    if _is_orion_chat_wake_group(agent, thread_id):
        return ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT
    if _is_chatgpt_work_wake_group(agent, thread_id):
        return ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT
    return ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT


def _orion_pending_coalesce_blocks(
    conn: sqlite3.Connection,
    *,
    agent: str,
    thread_id: str,
    debounce_before: str,
) -> bool:
    """Hold Orion claims until the burst coalesce window elapses (fresh batches only)."""
    if not _is_orion_chat_wake_group(agent, thread_id):
        return False
    row = conn.execute(
        "SELECT MIN(datetime(created_at)) AS oldest FROM endpoint_wake_ledger "
        "WHERE agent=? AND thread_id=? AND state='pending'",
        (agent, thread_id),
    ).fetchone()
    oldest = row["oldest"] if row else None
    if oldest is None or oldest <= debounce_before:
        return False
    prior = conn.execute(
        "SELECT 1 FROM endpoint_wake_ledger "
        "WHERE lower(trim(agent))=lower(trim(?)) AND thread_id=? "
        "AND state='delivered' AND datetime(finished_at) < ? LIMIT 1",
        (agent, thread_id, oldest),
    ).fetchone()
    return prior is None


def _endpoint_wake_tab_coalesce_s() -> float:
    raw = os.environ.get(
        "ENDPOINT_WAKE_TAB_COALESCE_S", str(ENDPOINT_WAKE_TAB_COALESCE_S_DEFAULT)
    )
    try:
        value = float(raw)
    except ValueError:
        return ENDPOINT_WAKE_TAB_COALESCE_S_DEFAULT
    if not math.isfinite(value) or value <= 0:
        return ENDPOINT_WAKE_TAB_COALESCE_S_DEFAULT
    return value


def _endpoint_wake_tab_coalesce_max_s() -> float:
    raw = os.environ.get(
        "ENDPOINT_WAKE_TAB_COALESCE_MAX_S", str(ENDPOINT_WAKE_TAB_COALESCE_MAX_S_DEFAULT)
    )
    try:
        value = float(raw)
    except ValueError:
        return ENDPOINT_WAKE_TAB_COALESCE_MAX_S_DEFAULT
    if not math.isfinite(value) or value <= 0:
        return ENDPOINT_WAKE_TAB_COALESCE_MAX_S_DEFAULT
    return value


def _tab_coalesce_applies_to_agent(agent: str) -> bool:
    """Per-tab burst merge skips Orion and web-anthropic / maestro alias mailboxes."""
    if normalize_bus_address(agent) == "orion":
        return False
    if normalize_bus_address(agent) in MAESTRO_WEB_ALIAS_PAIR:
        return False
    return True


def _tab_hold_from_rows(
    rows: Sequence[sqlite3.Row],
    *,
    delivery_agent_id: str,
    stamp: str,
) -> bool:
    """Hold until quiet window or max-hold over eligible tab-id rows only."""
    tab_id = str(delivery_agent_id or "").strip()
    if not tab_id:
        return False
    eligible = [
        r for r in rows if str(r["delivery_agent_id"] or "").strip() == tab_id
    ]
    if not eligible:
        return False
    oldest = min(str(r["created_at"]) for r in eligible)
    newest = max(str(r["created_at"]) for r in eligible)
    quiet_before = _shift(stamp, -int(_endpoint_wake_tab_coalesce_s()))
    max_hold_before = _shift(stamp, -int(_endpoint_wake_tab_coalesce_max_s()))
    return newest > quiet_before and oldest > max_hold_before


def _due_wake_group_sql(orion_next_gate: str, *, delivery_agent_id: str | None) -> str:
    delivery_sql = ""
    if delivery_agent_id is not None:
        delivery_sql = " AND COALESCE(p.delivery_agent_id,'')=? AND p.thread_id != '19627' "
    return f"""
                SELECT agent, thread_id FROM endpoint_wake_ledger p
                WHERE p.state='pending'
                  AND {_pending_retry_ready_sql("p")}
                  {orion_next_gate}
                  {delivery_sql}
                  AND NOT EXISTS (
                    SELECT 1 FROM endpoint_wake_ledger d
                    WHERE {_mailbox_agent_sql("d", "p")} AND d.thread_id=p.thread_id
                      AND d.state='delivered' AND d.finished_at> CASE
                        WHEN lower(trim(p.agent))='orion' AND p.thread_id=?
                        THEN ?
                        ELSE ?
                      END
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
                      AND u.state='unknown' AND u.claimed_at> CASE
                        WHEN lower(trim(p.agent))='orion' AND p.thread_id=?
                        THEN ?
                        ELSE ?
                      END
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM endpoint_wake_ledger c
                    WHERE {_mailbox_agent_sql("c", "p")} AND c.thread_id=p.thread_id
                      AND c.state='claimed' AND c.claimed_at>?
                  )
                GROUP BY agent, thread_id
                ORDER BY MIN(COALESCE(p.finished_at, '')), MIN(p.id)
                """


def _due_wake_group_params(
    *,
    stamp: str,
    retry_before: str,
    orion_next_params: tuple[Any, ...],
    debounce_before_orion: str,
    debounce_before_default: str,
    ttl_before: str,
    delivery_agent_id: str | None,
) -> tuple[Any, ...]:
    base: tuple[Any, ...] = (
        stamp,
        retry_before,
        *orion_next_params,
        _ORION_WAKE_HOUSE,
        debounce_before_orion,
        debounce_before_default,
        _ORION_WAKE_HOUSE,
        debounce_before_orion,
        debounce_before_default,
        ttl_before,
    )
    if delivery_agent_id is not None:
        return (
            stamp,
            retry_before,
            *orion_next_params,
            delivery_agent_id,
            _ORION_WAKE_HOUSE,
            debounce_before_orion,
            debounce_before_default,
            _ORION_WAKE_HOUSE,
            debounce_before_orion,
            debounce_before_default,
            ttl_before,
        )
    return base


def _wake_unbound_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    envelope.update({"state": "unbound", "current": None, "reason": "unbound"})
    return envelope


def _orion_subscription_for_thread(
    thread_id: str,
) -> tuple[str, dict[str, Any]] | None:
    """Live orion or chatgpt-surface subscription on the house thread (or parent scope)."""
    from web_chat_relay.orion_wake import (
        conversation_id_from_url,
        holder_conversation_url,
    )

    for agent_name in ("orion", "cursor"):
        resolved = resolve_subscription_for_thread(agent_name, thread_id)
        if resolved is None:
            continue
        route, row = resolved
        if str(row.get("state") or "") != "subscribed":
            continue
        if agent_name == "orion":
            if _orion_chat_actuation_from_row(row) is not None:
                return route, row
            continue
        surface = str(row.get("surface") or "").strip().lower()
        if surface == "chatgpt":
            return route, row
        url = holder_conversation_url(row)
        if "chatgpt.com" in url and conversation_id_from_url(url):
            return route, row
    return None


def _explicit_orion_conversation_url(row: dict[str, Any]) -> str | None:
    """ChatGPT /c/ URL from row fields only (no pinned-conversation fallback)."""
    from web_chat_relay.orion_wake import (
        _explicit_holder_url,
        _matching_explicit_url_for_uuid,
        conversation_id_from_url,
    )

    surface = str(row.get("surface") or "").strip().lower()
    for key in ("agent_id", "title", "chat_url"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        explicit = _explicit_holder_url(raw)
        if explicit is not None:
            return explicit.split("?", 1)[0]
        if re.fullmatch(r"[0-9a-f-]{36}", raw, flags=re.I):
            if key == "agent_id" and surface == "cursor":
                # Cursor Glass tab ids are UUIDs — not ChatGPT /c/ conversation ids.
                continue
            if key == "agent_id":
                matched = _matching_explicit_url_for_uuid(row, raw)
                if matched is not None:
                    return matched.split("?", 1)[0]
            return f"https://chatgpt.com/c/{raw}".split("?", 1)[0]
        cid = conversation_id_from_url(raw)
        if cid:
            return f"https://chatgpt.com/c/{cid}".split("?", 1)[0]
    return None


def _orion_chat_actuation_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    from web_chat_relay.orion_wake import (
        DEFAULT_ORION_CDP_URL,
        conversation_id_from_url,
    )

    url = _explicit_orion_conversation_url(row)
    if url is None:
        return None
    conv_id = conversation_id_from_url(url)
    if not conv_id:
        return None
    return {
        "type": "orion_chat",
        "conversation_id": conv_id,
        "conversation_url": url,
        "cdp_url": DEFAULT_ORION_CDP_URL,
    }


def _orion_actuation_from_conversation_url(url: str) -> dict[str, Any] | None:
    from web_chat_relay.orion_wake import (
        DEFAULT_ORION_CDP_URL,
        _strip_conversation_url,
        conversation_id_from_url,
    )

    clean = _strip_conversation_url(url)
    conv_id = conversation_id_from_url(clean)
    if not conv_id:
        return None
    return {
        "type": "orion_chat",
        "conversation_id": conv_id,
        "conversation_url": clean,
        "cdp_url": DEFAULT_ORION_CDP_URL,
    }


def _latest_orion_seat_registration_on_thread(
    thread_id: str,
) -> tuple[str, str] | None:
    """SHADOW (non-routing): scan bus text only; never used for wake delivery."""
    from web_chat_relay.orion_wake import extract_orion_seat_registration_url

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT subject, body, created_at
            FROM turns
            WHERE thread=?
              AND (
                subject LIKE 'SEAT_REGISTRATION%'
                OR body LIKE '%New exact URL:%'
                OR body LIKE 'TYPE: SEAT_REGISTRATION%'
              )
            ORDER BY turn_number DESC, id DESC
            LIMIT 20
            """,
            (thread_id,),
        ).fetchall()
    for row in rows:
        url = extract_orion_seat_registration_url(
            str(row["body"] or ""),
            str(row["subject"] or ""),
        )
        if url:
            return url, str(row["created_at"] or "")
    return None


def _orion_house_actuation_basis(thread_id: str, actuation: dict[str, Any]) -> str:
    from web_chat_relay.orion_wake import PINNED_CONVERSATION_ID

    conv_id = str(actuation.get("conversation_id") or "")
    if conv_id == PINNED_CONVERSATION_ID:
        return "orion_pinned_conversation"
    if _orion_subscription_for_thread(thread_id) is not None:
        return "agent_endpoint_subscription"
    return "unresolved"


def _orion_chat_actuation_for_house(thread_id: str) -> dict[str, Any] | None:
    live = _orion_subscription_for_thread(thread_id)

    sub_actuation: dict[str, Any] | None = None
    sub_row: dict[str, Any] | None = None
    if live is not None:
        _, sub_row = live
        sub_actuation = _orion_chat_actuation_from_row(sub_row)

    if sub_actuation is not None:
        return sub_actuation
    if thread_id != _ORION_WAKE_HOUSE:
        return None
    from web_chat_relay.orion_wake import (
        DEFAULT_ORION_CDP_URL,
        PINNED_CONVERSATION_ID,
        PINNED_CONVERSATION_URL,
    )

    url = PINNED_CONVERSATION_URL.split("?", 1)[0]
    return {
        "type": "orion_chat",
        "conversation_id": PINNED_CONVERSATION_ID,
        "conversation_url": url,
        "cdp_url": DEFAULT_ORION_CDP_URL,
    }


def _chatgpt_work_actuation_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    from web_chat_relay.orion_wake import (
        DEFAULT_ORION_CDP_URL,
        conversation_id_from_url,
    )

    if str(row.get("actuation") or "") != "chatgpt_work":
        return None
    url = str(row.get("title") or "").split("?", 1)[0].strip()
    conv_id = conversation_id_from_url(url)
    if not url or not conv_id:
        return None
    instance_id = str(row.get("agent") or "")
    if instance_id == "main-wake":
        instance_id = ""
    return {
        "type": "chatgpt_work",
        "conversation_id": conv_id,
        "conversation_url": url,
        "cdp_url": DEFAULT_ORION_CDP_URL,
        "mode": "work",
        "instance_id": instance_id or None,
    }


def _envelope_from_work_row(
    envelope: dict[str, Any],
    row: dict[str, Any],
    *,
    route: str = "chatgpt-work",
) -> dict[str, Any]:
    actuation = _chatgpt_work_actuation_from_row(row)
    if actuation is None:
        return _wake_unbound_envelope(envelope)
    envelope.update(
        {
            "state": "bound",
            "basis": "chatgpt_work",
            "current": {
                "actuation": actuation,
                "route": route,
                "subscription_id": row.get("id"),
            },
        }
    )
    return envelope


def resolve_orion_chat_wake(
    house_id: str, *, agent: str | None = None
) -> dict[str, Any]:
    """Sole journal authority for Orion / ChatGPT Work wake URLs."""
    from agent_bus_store.db.endpoint_subscriptions import (
        instance_wake_latest,
        main_wake_latest,
    )

    thread_id = str(house_id or "").strip()
    as_of = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scope = f"house:{thread_id}/intent:wake"
    envelope: dict[str, Any] = {
        "as_of": as_of,
        "basis": "unbound",
        "source": _DELIVERY_BINDING_SOURCE,
        "scope": scope,
        "stale": False,
    }
    if not thread_id:
        return _wake_unbound_envelope(envelope)

    if (
        agent is not None
        and normalize_bus_address(agent) == "orion"
        and str(thread_id) != _ORION_WAKE_HOUSE
    ):
        inst_row = instance_wake_latest(thread_id)
        if inst_row is not None:
            if str(inst_row.get("state") or "") != "subscribed":
                envelope.update(
                    {"state": "unbound", "current": None, "reason": "terminal_head"}
                )
                return envelope
            if _chatgpt_work_actuation_from_row(inst_row) is None:
                envelope.update(
                    {"state": "unbound", "current": None, "reason": "terminal_head"}
                )
                return envelope
            return _envelope_from_work_row(envelope, inst_row)

    if str(thread_id) == _ORION_WAKE_HOUSE:
        main_row = main_wake_latest()
        if main_row is not None:
            if str(main_row.get("state") or "") != "subscribed":
                envelope.update(
                    {"state": "unbound", "current": None, "reason": "terminal_head"}
                )
                return envelope
            if _chatgpt_work_actuation_from_row(main_row) is None:
                envelope.update(
                    {"state": "unbound", "current": None, "reason": "terminal_head"}
                )
                return envelope
            return _envelope_from_work_row(envelope, main_row)
        pin = _orion_chat_actuation_for_house(thread_id)
        if pin is not None:
            envelope.update(
                {
                    "state": "bound",
                    "basis": "orion_pinned_conversation",
                    "current": {"actuation": pin, "route": "orion-house"},
                }
            )
            return envelope

    return _wake_unbound_envelope(envelope)


def _is_chatgpt_work_wake_group(agent: str, thread_id: str) -> bool:
    if normalize_bus_address(agent) != "orion":
        return False
    binding = resolve_orion_chat_wake(thread_id, agent=agent)
    return str(binding.get("basis") or "") == "chatgpt_work"


def _cursor_glass_actuation(row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("surface") or "").strip().lower() != "cursor":
        return None
    display = str(row.get("display") or "")
    gui_host = normalize_gui_host(display)
    cdp_base = cdp_origin_for_gui_host(gui_host)
    return {
        "type": "cursor_glass",
        "gui_host": gui_host,
        "agent_id": str(row.get("agent_id") or ""),
        "cdp_base": cdp_base,
    }


def _authoritative_chatgpt_journal_row(
    thread_id: str, agent: str | None
) -> dict[str, Any] | None:
    from agent_bus_store.db.endpoint_subscriptions import (
        instance_wake_latest,
        main_wake_latest,
    )

    if thread_id == _ORION_WAKE_HOUSE:
        return main_wake_latest()
    if agent is not None and normalize_bus_address(agent) == "orion":
        return instance_wake_latest(thread_id)
    return None


def resolve_wake_delivery_binding(
    *, house_id: str, agent: str | None = None
) -> dict[str, Any]:
    """Resolve wake actuation from the subscribed endpoint row only (no CSE hop)."""
    thread_id = str(house_id or "").strip()
    as_of = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scope = f"house:{thread_id}/intent:wake"
    envelope: dict[str, Any] = {
        "as_of": as_of,
        "basis": "agent_endpoint_subscription",
        "source": _DELIVERY_BINDING_SOURCE,
        "scope": scope,
        "stale": False,
    }
    if not thread_id:
        return _wake_unbound_envelope(envelope)
    if _authoritative_chatgpt_journal_row(thread_id, agent) is not None:
        return resolve_orion_chat_wake(thread_id, agent=agent)
    resolved_orion = resolve_subscription_for_thread("orion", thread_id)
    if resolved_orion is not None:
        route, row = resolved_orion
        if str(row.get("state") or "") == "subscribed":
            orion_actuation = _orion_chat_actuation_from_row(row)
            if orion_actuation is not None:
                envelope.update(
                    {
                        "state": "bound",
                        "current": {
                            "actuation": orion_actuation,
                            "route": route,
                            "subscription_id": row.get("id"),
                        },
                    }
                )
                return envelope
    resolved = resolve_subscription_for_thread(_WAKE_DELIVERY_AGENT, thread_id)
    if resolved is not None:
        route, row = resolved
        if str(row.get("state") or "") == "subscribed":
            orion_from_cursor = _orion_chat_actuation_from_row(row)
            if orion_from_cursor is not None and (
                str(row.get("surface") or "").strip().lower() == "chatgpt"
                or "chatgpt.com" in orion_from_cursor.get("conversation_url", "")
            ):
                envelope.update(
                    {
                        "state": "bound",
                        "current": {
                            "actuation": orion_from_cursor,
                            "route": route,
                            "subscription_id": row.get("id"),
                        },
                    }
                )
                return envelope
            actuation = _cursor_glass_actuation(row)
            if actuation is not None and thread_id != _ORION_WAKE_HOUSE:
                envelope.update(
                    {
                        "state": "bound",
                        "current": {
                            "actuation": actuation,
                            "route": route,
                            "subscription_id": row.get("id"),
                        },
                    }
                )
                return envelope
    live_orion = _orion_subscription_for_thread(thread_id)
    if live_orion is not None:
        route, row = live_orion
        orion_actuation = _orion_chat_actuation_from_row(row)
        if orion_actuation is not None:
            envelope.update(
                {
                    "state": "bound",
                    "current": {
                        "actuation": orion_actuation,
                        "route": route,
                        "subscription_id": row.get("id"),
                    },
                }
            )
            return envelope
    journal = resolve_orion_chat_wake(thread_id, agent=agent)
    if str(journal.get("state") or "") == "bound":
        return journal
    if journal.get("reason") == "terminal_head":
        return journal
    return _wake_unbound_envelope(envelope)


_STEER_DELIVERY_AGENT = "cursor"
_STEER_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _live_cursor_subscription_for_steer(thread_id: str) -> bool:
    resolved = resolve_subscription_for_thread(_STEER_DELIVERY_AGENT, thread_id)
    if resolved is None:
        return False
    _, row = resolved
    return str(row.get("state") or "") == "subscribed"


def resolve_steer_delivery_binding(*, house_id: str) -> dict[str, Any]:
    """Resolve steer actuation from the cursor-sdk dispatch ledger (GIW store)."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        read_active_resume_steer_dispatch,
        read_latest_dispatch_for_thread,
    )

    thread_id = str(house_id or "").strip()
    as_of = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scope = f"house:{thread_id}/intent:steer"
    envelope: dict[str, Any] = {
        "as_of": as_of,
        "basis": "dispatch_ledger_active",
        "source": _DELIVERY_BINDING_SOURCE,
        "scope": scope,
        "stale": False,
    }
    if not thread_id:
        envelope.update({"state": "unbound", "current": None})
        return envelope

    active = read_active_resume_steer_dispatch(thread_id=thread_id)
    if active is not None:
        envelope.update(
            {
                "state": "bound",
                "current": {
                    "actuation": {
                        "type": "cursor_sdk_dispatch",
                        "dispatch_id": active["dispatch_id"],
                        "execution_id": active["execution_id"],
                        "resume_of": active["resume_of"],
                    },
                },
            }
        )
        return envelope

    latest = read_latest_dispatch_for_thread(thread_id=thread_id)
    if (
        latest is not None
        and str(latest["status"]) in _STEER_TERMINAL_STATUSES
        and _live_cursor_subscription_for_steer(thread_id)
    ):
        envelope.update({"state": "unbound", "current": None})
        return envelope

    envelope.update({"state": "unbound", "current": None})
    return envelope


def _read_inform_seat_rows_from_ask_owner() -> tuple[list[Any] | None, str | None]:
    """Return ask-owner ``seat_rows`` or ``(None, failure_reason)`` on miss."""
    from cdp_ask.client import CdpAskClient, CdpAskClientError, project_ask_base_url
    from cdp_ask.lane_snapshot import CONTROL_PLANE_TIMEOUT_S

    if not project_ask_base_url():
        return None, "ask_owner_unconfigured"
    try:
        client = CdpAskClient(timeout_s=CONTROL_PLANE_TIMEOUT_S)
        snap = client._request("GET", "/v1/project-ask/active-work")
    except CdpAskClientError:
        return None, "ask_owner_unreachable"
    if not isinstance(snap, dict):
        return None, "ask_owner_invalid_response"
    seat_rows = snap.get("seat_rows")
    if not isinstance(seat_rows, list):
        return None, "ask_owner_seat_rows_absent"
    return seat_rows, None


def _inform_unbound_current(
    *,
    reason: str,
    candidate_ids: list[str],
    house_id: str,
) -> dict[str, Any]:
    ids_text = ", ".join(candidate_ids) if candidate_ids else "(none)"
    return {
        "reason": reason,
        "candidates": list(candidate_ids),
        "receipt": (
            f"inform delivery binding for house {house_id}: unbound ({reason}); "
            f"candidate registration_ids: {ids_text}"
        ),
    }


def _select_inform_seat_row(
    seat_rows: list[Any],
    house_id: str,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Pick the open seat row on *house_id* with max ``seat_bound_at``."""
    house = house_id.strip()
    open_rows: list[dict[str, Any]] = []
    for row in seat_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("seat_lane") or "").strip() != house:
            continue
        if row.get("seat_closed_at") is not None:
            continue
        open_rows.append(row)
    if not open_rows:
        return None, None, []
    candidate_ids = [
        str(row.get("registration_id") or "").strip()
        for row in open_rows
        if str(row.get("registration_id") or "").strip()
    ]
    scored: list[tuple[dict[str, Any], float]] = []
    fields_absent = False
    for row in open_rows:
        bound_raw = row.get("seat_bound_at")
        if bound_raw is None:
            fields_absent = True
            continue
        try:
            bound = float(bound_raw)
        except (TypeError, ValueError):
            fields_absent = True
            continue
        scored.append((row, bound))
    if fields_absent or not scored:
        return None, "seat_fields_absent", candidate_ids
    max_bound = max(bound for _, bound in scored)
    winners = [row for row, bound in scored if bound == max_bound]
    if len(winners) > 1:
        tied_ids = [
            str(row.get("registration_id") or "").strip()
            for row in winners
            if str(row.get("registration_id") or "").strip()
        ]
        return None, "seat_ambiguous", tied_ids
    return winners[0], None, []


def resolve_inform_delivery_binding(*, house_id: str) -> dict[str, Any]:
    """Resolve bus poll lane from ask-owner active-work ``seat_rows``."""
    from claude_bundles.cdp_registry.session_address import chat_url_for_registration

    thread_id = str(house_id or "").strip()
    as_of = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scope = f"house:{thread_id}/intent:inform"
    envelope: dict[str, Any] = {
        "as_of": as_of,
        "basis": "cdp_registry_lane_holder",
        "source": _DELIVERY_BINDING_SOURCE,
        "scope": scope,
        "stale": False,
    }
    if not thread_id:
        envelope.update({"state": "unbound", "current": None})
        return envelope
    seat_rows, fetch_reason = _read_inform_seat_rows_from_ask_owner()
    if fetch_reason is not None:
        envelope.update(
            {
                "state": "unbound",
                "current": _inform_unbound_current(
                    reason=fetch_reason,
                    candidate_ids=[],
                    house_id=thread_id,
                ),
            }
        )
        return envelope
    assert seat_rows is not None
    row, unbound_reason, candidate_ids = _select_inform_seat_row(seat_rows, thread_id)
    if row is None:
        if unbound_reason is None:
            envelope.update({"state": "unbound", "current": None})
            return envelope
        envelope.update(
            {
                "state": "unbound",
                "current": _inform_unbound_current(
                    reason=unbound_reason,
                    candidate_ids=candidate_ids,
                    house_id=thread_id,
                ),
            }
        )
        return envelope
    registration_id = str(row.get("registration_id") or "").strip()
    lane_thread = str(row.get("parent_thread") or "").strip() or thread_id
    current: dict[str, Any] = {
        "actuation": {
            "type": "bus_poll",
            "thread_id": lane_thread,
        },
        "registration_id": registration_id,
    }
    chat_url = str(row.get("chat_url") or "").strip() or None
    if not chat_url and registration_id:
        chat_url = chat_url_for_registration(registration_id)
    if chat_url:
        current["chat_url"] = chat_url
    envelope.update({"state": "bound", "current": current})
    return envelope


def claim_due_wakes(
    debounce_s: int = ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT,
    claim_ttl_s: int = 300,
    limit: int = 1,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Claim one (agent, thread) group, skipping NF2 rows in the same write."""
    _ = debounce_s  # dispatcher passes a floor; per-kind windows are server-side
    stamp = _now(now)
    debounce_before_default = _shift(stamp, -ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT)
    debounce_before_orion = _shift(stamp, -ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT)
    ttl_before = _shift(stamp, -claim_ttl_s)
    retry_before = _shift(stamp, -60)
    with write_connect() as conn:
        cols = _ledger_columns(conn)
        orion_next_gate = ""
        orion_next_params: tuple[Any, ...] = ()
        group_next_sql = ""
        orion_group_params: tuple[Any, ...] = ()
        if "wake_next_attempt_at" in cols:
            from bus_watch.orion_wake_bounded_defer import (
                orion_bounded_defer_reasons_sql_sorted,
            )

            _reasons = orion_bounded_defer_reasons_sql_sorted()
            _bounded_in_sql = f"({','.join('?' * len(_reasons))})"
            orion_next_gate = (
                f" AND {_orion_composer_defer_claim_ready_sql('p', in_sql=_bounded_in_sql)}"
            )
            group_next_sql = (
                f" AND {_orion_composer_defer_claim_ready_sql('', in_sql=_bounded_in_sql)}"
            )
            orion_next_params = (*_reasons, stamp)
            orion_group_params = orion_next_params
        conn.execute(
            "UPDATE endpoint_wake_ledger SET state='unknown' "
            "WHERE state='claimed' AND claimed_at<=?",
            (ttl_before,),
        )
        all_skipped: list[dict[str, Any]] = []
        group_params_base = _due_wake_group_params(
            stamp=stamp,
            retry_before=retry_before,
            orion_next_params=orion_next_params,
            debounce_before_orion=debounce_before_orion,
            debounce_before_default=debounce_before_default,
            ttl_before=ttl_before,
            delivery_agent_id=None,
        )
        prefetched = conn.execute(
            _due_wake_group_sql(orion_next_gate, delivery_agent_id=None)
            + " LIMIT ?",
            (*group_params_base, _CLAIM_SKIP_GROUP_CAP),
        ).fetchall()
        held_tabs: set[str] = set()
        for group in prefetched:
            if _orion_pending_coalesce_blocks(
                conn,
                agent=str(group["agent"]),
                thread_id=str(group["thread_id"]),
                debounce_before=debounce_before_orion,
            ):
                continue
            rows = _group_pending_rows(
                conn,
                group,
                stamp=stamp,
                retry_before=retry_before,
                orion_next_gate_sql=group_next_sql,
                orion_next_extra_params=orion_group_params,
            )
            skipped: list[dict[str, Any]] = []
            claimable: list[sqlite3.Row] = []
            tab_delivery_id = ""
            if "delivery_agent_id" in cols:
                for row in rows:
                    candidate = str(row["delivery_agent_id"] or "").strip()
                    if candidate:
                        tab_delivery_id = candidate
                        break
            thread_id = str(group["thread_id"])
            tab_merge = bool(
                tab_delivery_id
                and _tab_coalesce_applies_to_agent(str(group["agent"]))
                and thread_id != "19627"
            )
            groups_to_merge = [group]
            merge_rows_pool: list[sqlite3.Row] = list(rows)
            if tab_merge:
                if tab_delivery_id in held_tabs:
                    continue
                merge_params = _due_wake_group_params(
                    stamp=stamp,
                    retry_before=retry_before,
                    orion_next_params=orion_next_params,
                    debounce_before_orion=debounce_before_orion,
                    debounce_before_default=debounce_before_default,
                    ttl_before=ttl_before,
                    delivery_agent_id=tab_delivery_id,
                )
                groups_to_merge = conn.execute(
                    _due_wake_group_sql(
                        orion_next_gate, delivery_agent_id=tab_delivery_id
                    ),
                    merge_params,
                ).fetchall()
                merge_rows_pool = []
                for grp in groups_to_merge:
                    merge_rows_pool.extend(
                        _group_pending_rows(
                            conn,
                            grp,
                            stamp=stamp,
                            retry_before=retry_before,
                            orion_next_gate_sql=group_next_sql,
                            orion_next_extra_params=orion_group_params,
                        )
                    )
                if _tab_hold_from_rows(
                    merge_rows_pool,
                    delivery_agent_id=tab_delivery_id,
                    stamp=stamp,
                ):
                    held_tabs.add(tab_delivery_id)
                    continue
            seen_claim_ids: set[int] = set()
            for grp in groups_to_merge:
                grp_rows = (
                    rows
                    if grp["agent"] == group["agent"]
                    and grp["thread_id"] == group["thread_id"]
                    else _group_pending_rows(
                        conn,
                        grp,
                        stamp=stamp,
                        retry_before=retry_before,
                        orion_next_gate_sql=group_next_sql,
                        orion_next_extra_params=orion_group_params,
                    )
                )
                for row in grp_rows:
                    row_id = int(row["id"])
                    if row_id in seen_claim_ids:
                        continue
                    from bus_watch.endpoint_wake_composer_defer import (
                        composer_defer_blocks_claim,
                    )

                    if composer_defer_blocks_claim(
                        reason=str(row["reason"] or ""),
                        decided_by_rule=str(row["decided_by_rule"] or ""),
                        agent=str(row["agent"] or ""),
                    ):
                        continue
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
                        seen_claim_ids.add(row_id)
                        claimable.append(row)
            if not claimable:
                all_skipped.extend(skipped)
                continue
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
                           turns.from_agent AS from_agent,
                           turns.to_agent AS to_agent
                    FROM endpoint_wake_ledger
                    LEFT JOIN turns ON turns.id = endpoint_wake_ledger.turn_id
                    WHERE endpoint_wake_ledger.wake_id=?
                    ORDER BY endpoint_wake_ledger.turn_id
                    """,
                    (wake_id,),
                ).fetchall()
            ]
            return {
                "wake_id": wake_id,
                "rows": claimed,
                "delivery_agent_id": tab_delivery_id if tab_merge else None,
            }
        return {"wake_id": None, "rows": all_skipped}


def parse_orion_deferred_attempt(decided_by_rule: str | None) -> int:
    """Return the stored deferred-streaming attempt count (0 when unset)."""
    text = str(decided_by_rule or "")
    if not text.startswith(_ORION_DEFERRED_ATTEMPT_PREFIX):
        return 0
    try:
        return max(0, int(text[len(_ORION_DEFERRED_ATTEMPT_PREFIX) :]))
    except ValueError:
        return 0


def parse_orion_composer_defer_attempt(decided_by_rule: str | None) -> int:
    text = str(decided_by_rule or "")
    if not text.startswith(_ORION_COMPOSER_DEFER_ATTEMPT_PREFIX):
        return 0
    try:
        return max(0, int(text[len(_ORION_COMPOSER_DEFER_ATTEMPT_PREFIX) :]))
    except ValueError:
        return 0


def orion_deferred_retry_delay_s(attempt: int) -> int:
    """Backoff before the next Orion deferred-streaming claim (bounded)."""
    n = max(1, int(attempt))
    return min(ORION_DEFERRED_RETRY_MAX_S, ORION_DEFERRED_RETRY_BASE_S * n)


def post_orion_wake_undelivered_notices(
    *,
    thread_id: str,
    turn_numbers: list[int],
) -> None:
    """Post one exhaustion NOTICE per maestro lane (12286 + 21220)."""
    from .turns import insert_turn

    turns_s = ",".join(str(n) for n in sorted({int(t) for t in turn_numbers}))
    body = f"thread={thread_id} turns={turns_s} receipt=deferred_exhausted"
    subject = "NOTICE|orion wake undelivered"
    for lane in ("12286", "21220"):
        insert_turn(
            thread=lane,
            from_agent="agent_bus",
            to_agent="maestro",
            subject=subject,
            body=body,
        )


def record_orion_deferred_streaming(
    wake_id: str,
    route: str | None,
    subscription_id: int | None,
    cse_registration_id: str | None,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Schedule a ledger retry for streaming deferral, or exhaust with NOTICE."""
    stamp = _now(now)
    with write_connect() as conn:
        rows = conn.execute(
            "SELECT id, state, thread_id, turn_number, decided_by_rule "
            "FROM endpoint_wake_ledger WHERE wake_id=?",
            (wake_id,),
        ).fetchall()
        eligible = [r for r in rows if r["state"] in _CLAIM_STATES]
        if not eligible:
            raise WakeConflictError("wake_not_claimed")
        prior = max(
            parse_orion_deferred_attempt(r["decided_by_rule"]) for r in eligible
        )
        attempt = prior + 1
        thread_id = str(eligible[0]["thread_id"])
        turn_numbers = sorted({int(r["turn_number"]) for r in eligible})
        ids = [int(r["id"]) for r in eligible]
        cols = _ledger_columns(conn)
        if attempt > ORION_DEFERRED_STREAMING_MAX_ATTEMPTS:
            telem_sets = ""
            telem_vals: tuple[Any, ...] = ()
            if "wake_last_attempt_at" in cols:
                telem_sets += ", wake_last_attempt_at=?"
                telem_vals += (stamp,)
            if "wake_next_attempt_at" in cols:
                telem_sets += ", wake_next_attempt_at=?"
                telem_vals += (None,)
            if "wake_attempt_count" in cols:
                telem_sets += ", wake_attempt_count=?"
                telem_vals += (attempt,)
            conn.execute(
                f"UPDATE endpoint_wake_ledger SET state='refused', reason='deferred_exhausted', "
                f"route=?, subscription_id=?, cse_registration_id=?, finished_at=?, "
                f"decided_by_rule=?{telem_sets} WHERE id IN ({','.join('?' * len(ids))})",
                (
                    route,
                    subscription_id,
                    cse_registration_id,
                    stamp,
                    f"{_ORION_DEFERRED_ATTEMPT_PREFIX}{attempt}",
                    *telem_vals,
                    *ids,
                ),
            )
            post_orion_wake_undelivered_notices(
                thread_id=thread_id, turn_numbers=turn_numbers
            )
            return {
                "exhausted": True,
                "attempt": attempt,
                "thread_id": thread_id,
                "turn_numbers": turn_numbers,
            }
        retry_at = _shift(stamp, orion_deferred_retry_delay_s(attempt))
        telem_sets = ""
        telem_vals = ()
        if "wake_last_attempt_at" in cols:
            telem_sets += ", wake_last_attempt_at=?"
            telem_vals += (stamp,)
        if "wake_next_attempt_at" in cols:
            telem_sets += ", wake_next_attempt_at=?"
            telem_vals += (retry_at,)
        if "wake_attempt_count" in cols:
            telem_sets += ", wake_attempt_count=?"
            telem_vals += (attempt,)
        conn.execute(
            f"UPDATE endpoint_wake_ledger SET state='pending', reason='deferred_streaming', "
            f"route=?, subscription_id=?, cse_registration_id=?, finished_at=?, "
            f"decided_by_rule=?, wake_id=NULL, claimed_at=NULL{telem_sets} "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            (
                route,
                subscription_id,
                cse_registration_id,
                retry_at,
                f"{_ORION_DEFERRED_ATTEMPT_PREFIX}{attempt}",
                *telem_vals,
                *ids,
            ),
        )
    return {
        "exhausted": False,
        "attempt": attempt,
        "thread_id": thread_id,
        "turn_numbers": turn_numbers,
        "retry_at": retry_at,
    }


def record_wake_result(
    wake_id: str,
    outcome: str,
    reason: str | None,
    route: str | None,
    subscription_id: int | None,
    cse_registration_id: str | None,
    *,
    decided_by_rule: str | None = None,
    wake_detail: dict[str, Any] | None = None,
    now: str | None = None,
) -> int:
    """Persist a result on claimed rows, or unknown rows folded from that claim."""
    from bus_watch.endpoint_wake_composer_defer import (
        is_composer_defer_reason,
        merge_composer_defer_decided_by_rule,
    )
    from bus_watch.orion_wake_bounded_defer import orion_bounded_defer_reason

    stamp = _now(now)
    detail = dict(wake_detail or {})
    merge_reason = str(detail.pop("composer_defer_reason", "") or "").strip() or str(
        reason or ""
    ).strip()
    decided_by_rule = merge_composer_defer_decided_by_rule(
        decided_by_rule, reason=merge_reason
    )
    outcome_in = str(outcome or "").strip().lower()
    reason_s = str(reason or "").strip()
    pending_handoff = outcome_in == "pending" and (
        is_composer_defer_reason(merge_reason)
        or orion_bounded_defer_reason(merge_reason)
    )
    release_claim = bool(detail.pop("release_claim_for_retry", False))
    if outcome_in == "refused":
        # friction:42978 — title_mismatch is terminal; other refused composer defers stay open.
        finished = stamp if reason_s == "title_mismatch" else None
    elif pending_handoff and release_claim:
        finished = None
    else:
        finished = stamp
    with write_connect() as conn:
        cols = _ledger_columns(conn)
        select_extra = ""
        if "wake_attempt_count" in cols:
            select_extra = ", wake_attempt_count"
        detail_select = (
            ", delivery_failure_detail" if "delivery_failure_detail" in cols else ""
        )
        rows = conn.execute(
            f"SELECT id, state, agent, thread_id, turn_number, subscription_id, reason, "
            f"decided_by_rule{detail_select}{select_extra} "
            f"FROM endpoint_wake_ledger WHERE wake_id=?",
            (wake_id,),
        ).fetchall()
        eligible = [r for r in rows if r["state"] in _CLAIM_STATES]
        if not eligible:
            raise WakeConflictError("wake_not_claimed")
        ids = [int(r["id"]) for r in eligible]
        prior_attempt = 0
        for r in eligible:
            if "wake_attempt_count" in cols and r["wake_attempt_count"] is not None:
                try:
                    prior_attempt = max(prior_attempt, int(r["wake_attempt_count"]))
                except (TypeError, ValueError):
                    pass
            prior_attempt = max(
                prior_attempt, parse_orion_deferred_attempt(r["decided_by_rule"])
            )
        next_attempt = detail.pop("wake_next_attempt_at", None)
        last_attempt = detail.pop("wake_last_attempt_at", None)
        if last_attempt is None:
            last_attempt = stamp
        attempt_count = detail.pop("wake_attempt_count", None)
        if attempt_count is None and "wake_attempt_count" in cols:
            attempt_count = prior_attempt + 1
        sub_for_row = subscription_id
        if sub_for_row is None and "subscription_id" in cols:
            for r in eligible:
                sid = r["subscription_id"]
                if sid is not None:
                    sub_for_row = int(sid)
                    break
        # Single authoritative writer: dispatcher must not overwrite owner unknown.
        prior_row = eligible[0]
        prior_reason = str(prior_row["reason"] or "")
        if prior_reason.startswith("paste_receipt:"):
            try:
                blob = json.loads(prior_reason[len("paste_receipt:") :])
            except json.JSONDecodeError:
                blob = {}
            if (
                isinstance(blob, dict)
                and str(blob.get("outcome") or "") == "unknown"
                and outcome_in in {"refused", "unknown", "delivered", "pending", "skipped"}
            ):
                return 0
        # Merge with prior delivery_failure_detail when the new payload is thinner.
        prior_detail: dict[str, Any] = {}
        if "delivery_failure_detail" in cols:
            raw_prior = prior_row["delivery_failure_detail"]
            if raw_prior:
                try:
                    parsed = json.loads(str(raw_prior))
                    if isinstance(parsed, dict):
                        prior_detail = parsed
                except json.JSONDecodeError:
                    prior_detail = {}
        merged_detail = {**prior_detail, **detail}
        detail_blob = _encode_delivery_failure_detail(merge_reason, **merged_detail)
        row_reason = (
            merge_reason
            if pending_handoff and release_claim
            else reason_s
        )
        set_rule = (
            ", decided_by_rule=COALESCE(?, decided_by_rule)" if decided_by_rule else ""
        )
        telem = ""
        telem_params: list[Any] = []
        if "wake_last_attempt_at" in cols:
            telem += ", wake_last_attempt_at=?"
            telem_params.append(last_attempt)
        if "wake_next_attempt_at" in cols:
            telem += ", wake_next_attempt_at=?"
            telem_params.append(next_attempt)
        if "wake_attempt_count" in cols:
            telem += ", wake_attempt_count=?"
            telem_params.append(attempt_count)
        if "delivery_failure_detail" in cols:
            telem += ", delivery_failure_detail=?"
            telem_params.append(detail_blob)
        params: list[Any] = [
            outcome_in,
            row_reason,
            route,
            sub_for_row,
            cse_registration_id,
            finished,
        ]
        if decided_by_rule:
            params.append(decided_by_rule)
        params.extend(telem_params)
        params.extend(ids)
        conn.execute(
            f"UPDATE endpoint_wake_ledger SET state=?, reason=?, route=?, "
            f"subscription_id=?, cse_registration_id=?, finished_at=?{set_rule}{telem} "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            tuple(params),
        )
        if pending_handoff and release_claim:
            from bus_watch.endpoint_wake_composer_defer import COMPOSER_DEFER_ARMED

            comp_prior = 0
            for r in eligible:
                comp_prior = max(
                    comp_prior, parse_orion_composer_defer_attempt(r["decided_by_rule"])
                )
                if "wake_attempt_count" in cols and r["wake_attempt_count"] is not None:
                    try:
                        comp_prior = max(comp_prior, int(r["wake_attempt_count"]))
                    except (TypeError, ValueError):
                        pass
            ac_val = max(1, comp_prior + 1)
            next_wall = _shift(stamp, orion_deferred_retry_delay_s(ac_val))
            orion_agent = normalize_bus_address(str(eligible[0]["agent"])) == "orion"
            exhausted_orion = orion_agent and orion_bounded_defer_reason(
                merge_reason
            ) and ac_val > ORION_COMPOSER_DEFER_MAX_ATTEMPTS
            if exhausted_orion:
                thread_id = str(eligible[0]["thread_id"])
                turn_numbers = sorted({int(r["turn_number"]) for r in eligible})
                ex_sets = (
                    "state='refused', reason='composer_defer_exhausted', wake_id=NULL, "
                    "claimed_at=NULL, finished_at=?"
                )
                ex_params: list[Any] = [stamp]
                if "wake_last_attempt_at" in cols:
                    ex_sets += ", wake_last_attempt_at=?"
                    ex_params.append(stamp)
                if "wake_next_attempt_at" in cols:
                    ex_sets += ", wake_next_attempt_at=?"
                    ex_params.append(None)
                if "wake_attempt_count" in cols:
                    ex_sets += ", wake_attempt_count=?"
                    ex_params.append(ac_val)
                ex_sets += ", decided_by_rule=?"
                ex_params.append(f"{_ORION_COMPOSER_DEFER_ATTEMPT_PREFIX}{ac_val}")
                conn.execute(
                    f"UPDATE endpoint_wake_ledger SET {ex_sets} "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    (*ex_params, *ids),
                )
                post_orion_wake_undelivered_notices(
                    thread_id=thread_id,
                    turn_numbers=turn_numbers,
                )
            else:
                release_sets = (
                    "state='pending', wake_id=NULL, claimed_at=NULL, "
                    "reason=?, decided_by_rule=?"
                )
                release_params: list[Any] = [merge_reason, COMPOSER_DEFER_ARMED]
                if "wake_last_attempt_at" in cols:
                    release_sets += ", wake_last_attempt_at=?"
                    release_params.append(stamp)
                if "wake_next_attempt_at" in cols:
                    release_sets += ", wake_next_attempt_at=?"
                    release_params.append(next_wall)
                if "wake_attempt_count" in cols:
                    release_sets += ", wake_attempt_count=?"
                    release_params.append(ac_val)
                elif "wake_next_attempt_at" not in cols:
                    release_sets += ", finished_at=?"
                    release_params.append(next_wall)
                conn.execute(
                    f"UPDATE endpoint_wake_ledger SET {release_sets} "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    (*release_params, *ids),
                )
        try:
            from agent_bus_store.events.publisher import emit

            emit(
                "endpoint_wake.lifecycle",
                {
                    "wake_id": wake_id,
                    "outcome": outcome_in,
                    "reason": merge_reason,
                    "subscription_id": sub_for_row,
                    "attempt_count": attempt_count,
                    "wake_next_attempt_at": next_attempt,
                },
            )
        except Exception:
            pass
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
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(turns)").fetchall()
        }
        from_col = ", from_agent_id" if "from_agent_id" in cols else ""
        rows = conn.execute(
            f"SELECT id, thread, turn_number, from_agent, to_agent, subject, "
            f"status, read_at, created_at{from_col} FROM turns WHERE id>? "
            "ORDER BY id ASC LIMIT ?",
            (after_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_delivered_agent_ids(
    thread_id: str,
    window_s: int,
    *,
    before: str | None = None,
    now: str | None = None,
) -> list[str]:
    """Distinct tab ids with delivered wakes in ``[before-window_s, before]`` (UTC stamps)."""
    if window_s <= 0:
        return []
    anchor = _now(before or now)
    with connect() as conn:
        cols = _ledger_columns(conn)
        if "delivery_agent_id" not in cols:
            return []
        rows = conn.execute(
            "SELECT DISTINCT delivery_agent_id FROM endpoint_wake_ledger "
            "WHERE thread_id=? AND state='delivered' "
            "AND delivery_agent_id IS NOT NULL AND trim(delivery_agent_id) != '' "
            "AND datetime(finished_at) >= datetime(?, ?) "
            "AND datetime(finished_at) <= datetime(?)",
            (thread_id, anchor, f"-{window_s} seconds", anchor),
        ).fetchall()
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        aid = str(row["delivery_agent_id"]).strip().lower()
        if aid and aid not in seen:
            seen.add(aid)
            out.append(aid)
    return out


def list_wake_ledger_rows(
    *,
    thread: str | None = None,
    turn_id: int | None = None,
    turn_number: int | None = None,
    agent: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Read-only wake ledger query for agent_bus_read ``wake_ledger``."""
    cap = max(1, min(int(limit), 200))
    clauses: list[str] = []
    params: list[Any] = []
    if thread is not None and str(thread).strip():
        clauses.append("thread_id = ?")
        params.append(str(thread).strip())
    if turn_id is not None:
        clauses.append("turn_id = ?")
        params.append(int(turn_id))
    if turn_number is not None:
        clauses.append("turn_number = ?")
        params.append(int(turn_number))
    if agent is not None and str(agent).strip():
        clauses.append("agent = ?")
        params.append(str(agent).strip())
    if state is not None and str(state).strip():
        clauses.append("state = ?")
        params.append(str(state).strip())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(cap)
    with connect() as conn:
        cols = _ledger_columns(conn)
        extra = [
            c
            for c in (
                "wake_last_attempt_at",
                "wake_next_attempt_at",
                "wake_attempt_count",
                "delivery_failure_detail",
            )
            if c in cols
        ]
        select_cols = (
            "id, agent, thread_id, turn_id, turn_number, state, wake_id, route, "
            "subscription_id, cse_registration_id, reason, decided_by_rule, "
            "created_at, claimed_at, finished_at"
        )
        if extra:
            select_cols += ", " + ", ".join(extra)
        sql = (
            f"SELECT {select_cols} FROM endpoint_wake_ledger{where} "
            "ORDER BY id DESC LIMIT ?"
        )
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["rule_id"] = item.pop("decided_by_rule", None)
        out.append(item)
    return out


def record_scan_drops(rows: list[dict[str, Any]], *, now: str | None = None) -> int:
    """Insert qualified-wake scan drops (separate from claim ledger rows).

    Uses ``INSERT OR IGNORE`` on ``(agent, thread_id, turn_id, reason)`` so repeat
    scans stay idempotent and never occupy the ledger unique key.
    """
    if not rows:
        return 0
    stamp = _now(now)
    inserted = 0
    with write_connect() as conn:
        for row in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO endpoint_wake_scan_drops "
                "(agent, thread_id, turn_id, turn_number, rule_id, reason, "
                "subscription_id, scanned_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(row["agent"]),
                    str(row["thread_id"]),
                    int(row["turn_id"]),
                    row.get("turn_number"),
                    row.get("rule_id"),
                    str(row["reason"]),
                    row.get("subscription_id"),
                    stamp,
                ),
            )
            inserted += int(cur.rowcount or 0)
    return inserted
