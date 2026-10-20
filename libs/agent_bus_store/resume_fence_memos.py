"""Unread memos the successor must check, since the previous checkpoint.

``threads`` has no ``parent_thread`` column. Depth-1 children are the current
row of ``thread_lane_associations`` (``MAX(id)`` per lane). Root rows are
scanned first; children share the leftover of the 400-row budget.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .bus_notice import classify_unread_turn
from .recipients import _inbox_recipient_slugs
from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL
from .db.connection import connect

_SCAN_LIMIT = 400
_SHOWN_CAP = 12
_BODY_CHARS = 600
_NOTE = (
    "Resume does not mark these read. After you have acted on or explicitly "
    "deferred each memo, run each mark_read entry as given."
)
_TRUNCATION_NOTE = (
    " Some memos were not shown (counts.truncated); the next resume lists the "
    "older ones, or read them now with agent_bus fetch_unread(thread=<thread>, "
    "to=<seat>)."
)
_NO_SEAT_NOTE = "no seat mailbox"

_TURN_COLS = "thread, turn_number, from_agent, to_agent, subject, body, created_at"


def _counts_zero() -> dict[str, int]:
    return {
        "unread_in_window": 0,
        "addressed": 0,
        "memos": 0,
        "shown": 0,
        "dropped_notice": 0,
        "dropped_own": 0,
        "not_addressed": 0,
        "truncated": 0,
        "scan_truncated": 0,
    }


def _window(prev_turn: int, prev_at: str | None, tip_turn: int) -> dict[str, Any]:
    return {
        "previous_checkpoint_turn": prev_turn,
        "previous_checkpoint_at": prev_at,
        "tip_turn": tip_turn,
        "through": "now",
    }


def _previous_checkpoint(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    tip_turn: int,
    supersedes_turn: int | None,
) -> tuple[int, str | None]:
    if supersedes_turn:
        row = conn.execute(
            "SELECT turn_number, created_at FROM turns WHERE id = ?",
            (int(supersedes_turn),),
        ).fetchone()
        if row is not None:
            return int(row["turn_number"]), row["created_at"]
    row = conn.execute(
        f"""
        SELECT turn_number, created_at FROM turns
        WHERE thread = ? AND {CHECKPOINT_SUBJECT_SQL} AND turn_number < ?
        ORDER BY turn_number DESC
        LIMIT 1
        """,
        (thread_id, int(tip_turn)),
    ).fetchone()
    if row is not None:
        return int(row["turn_number"]), row["created_at"]
    return 0, None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _threads_have_parent(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(threads)").fetchall()
    return any(str(row[1]) == "parent_thread" for row in rows)


def _fetch_root(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    prev_turn: int,
    limit: int,
) -> tuple[list[sqlite3.Row], bool]:
    rows = conn.execute(
        f"""
        SELECT {_TURN_COLS}
        FROM turns
        WHERE thread = ?
          AND turn_number > ?
          AND read_at IS NULL
          AND status != 'superseded'
          AND NOT ({CHECKPOINT_SUBJECT_SQL})
        ORDER BY turn_number DESC
        LIMIT ?
        """,
        (thread_id, prev_turn, limit + 1),
    ).fetchall()
    truncated = len(rows) > limit
    return list(rows[:limit]), truncated


def _fetch_children(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    prev_turn: int,
    prev_at: str | None,
    limit: int,
) -> tuple[list[sqlite3.Row], bool]:
    if limit <= 0:
        return [], False
    if _threads_have_parent(conn):
        sql = f"""
            SELECT {_TURN_COLS}
            FROM turns
            WHERE thread IN (SELECT id FROM threads WHERE parent_thread = ?)
              AND read_at IS NULL
              AND status != 'superseded'
              AND NOT ({CHECKPOINT_SUBJECT_SQL})
        """
        params: list[Any] = [thread_id]
    elif _table_exists(conn, "thread_lane_associations"):
        sql = f"""
            SELECT t.thread, t.turn_number, t.from_agent, t.to_agent,
                   t.subject, t.body, t.created_at
            FROM turns t
            INNER JOIN (
                SELECT tla.thread_id
                FROM thread_lane_associations tla
                INNER JOIN (
                    SELECT thread_id, MAX(id) AS max_id
                    FROM thread_lane_associations
                    GROUP BY thread_id
                ) latest
                    ON tla.thread_id = latest.thread_id
                   AND tla.id = latest.max_id
                WHERE tla.parent_thread_id = ?
            ) kids ON kids.thread_id = t.thread
            WHERE t.read_at IS NULL
              AND t.status != 'superseded'
              AND NOT ({CHECKPOINT_SUBJECT_SQL})
        """
        params = [thread_id]
    else:
        return [], False
    if prev_turn != 0:
        created_col = "created_at" if _threads_have_parent(conn) else "t.created_at"
        sql += f" AND {created_col} >= ?"
        params.append(prev_at or "")
    order = "created_at DESC, turn_number DESC"
    if not _threads_have_parent(conn):
        order = "t.created_at DESC, t.turn_number DESC"
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()
    truncated = len(rows) > limit
    return list(rows[:limit]), truncated


def _item(row: sqlite3.Row) -> dict[str, Any]:
    body = str(row["body"] or "")
    thread = str(row["thread"])
    turn = int(row["turn_number"])
    return {
        "ref": f"agent-bus:{thread}#{turn}",
        "thread": thread,
        "turn": turn,
        "from": str(row["from_agent"] or ""),
        "to": str(row["to_agent"] or ""),
        "subject": str(row["subject"] or ""),
        "created_at": row["created_at"],
        "body": body[:_BODY_CHARS],
        "body_truncated": len(body) > _BODY_CHARS,
    }


def build_memos_to_check(
    thread_id: str,
    *,
    tip_turn: int,
    tip_from_agent: str | None,
    supersedes_turn: int | None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Read-only memo block for the tip seat since the previous checkpoint.

    Notices and the seat's own outgoing turns are counted and dropped.
    At most 12 memos are shown, newest kept, oldest rendered first.
    """
    seat = (tip_from_agent or "").strip() or None

    def _build(db: sqlite3.Connection) -> dict[str, Any]:
        prev_turn, prev_at = _previous_checkpoint(
            db,
            thread_id,
            tip_turn=tip_turn,
            supersedes_turn=supersedes_turn,
        )
        window = _window(prev_turn, prev_at, int(tip_turn))
        if seat is None:
            return {
                "seat": None,
                "counts": _counts_zero(),
                "items": [],
                "window": window,
                "mark_read": [],
                "note": _NO_SEAT_NOTE,
            }
        seat_slugs = set(_inbox_recipient_slugs(seat))
        root_rows, root_hit = _fetch_root(
            db, thread_id, prev_turn=prev_turn, limit=_SCAN_LIMIT
        )
        child_rows, child_hit = _fetch_children(
            db,
            thread_id,
            prev_turn=prev_turn,
            prev_at=prev_at,
            limit=_SCAN_LIMIT - len(root_rows),
        )
        scanned = root_rows + child_rows
        counts = _counts_zero()
        counts["unread_in_window"] = len(scanned)
        counts["scan_truncated"] = int(root_hit or child_hit)
        memos: list[dict[str, Any]] = []
        addressed_turn: dict[str, int] = {}
        memo_count_by_thread: dict[str, int] = {}
        for row in scanned:
            kind = classify_unread_turn(
                from_agent=str(row["from_agent"] or ""),
                to_agent=str(row["to_agent"] or ""),
                subject=str(row["subject"] or ""),
                body=str(row["body"] or ""),
                seat_slugs=seat_slugs,
            )
            thread = str(row["thread"])
            turn = int(row["turn_number"])
            if kind == "not_addressed":
                counts["not_addressed"] += 1
                continue
            counts["addressed"] += 1
            addressed_turn[thread] = max(addressed_turn.get(thread, 0), turn)
            if kind == "notice":
                counts["dropped_notice"] += 1
            elif kind == "own":
                counts["dropped_own"] += 1
            else:
                counts["memos"] += 1
                memos.append(_item(row))
                memo_count_by_thread[thread] = memo_count_by_thread.get(thread, 0) + 1
        memos.sort(key=lambda item: (str(item["created_at"] or ""), int(item["turn"])))
        shown = memos[-_SHOWN_CAP:]
        counts["shown"] = len(shown)
        counts["truncated"] = counts["memos"] - counts["shown"]
        shown_by_thread: dict[str, list[int]] = {}
        for item in shown:
            shown_by_thread.setdefault(str(item["thread"]), []).append(
                int(item["turn"])
            )
        mark_read: list[dict[str, Any]] = []
        any_partial = False
        for thread in sorted(set(addressed_turn) | set(shown_by_thread)):
            shown_turns = sorted(shown_by_thread.get(thread, []))
            if not shown_turns:
                continue
            unshown_memos = memo_count_by_thread.get(thread, 0) - len(shown_turns)
            if unshown_memos > 0:
                any_partial = True
                mark_read.append(
                    {
                        "tool": "agent_bus",
                        "op": "mark_read",
                        "thread": thread,
                        "agent": seat,
                        "turn_numbers": shown_turns,
                        "partial": True,
                        "unshown_memos": unshown_memos,
                    }
                )
            else:
                mark_read.append(
                    {
                        "tool": "agent_bus",
                        "op": "mark_read",
                        "thread": thread,
                        "agent": seat,
                        "through_turn": addressed_turn[thread],
                    }
                )
        note = _NOTE + (_TRUNCATION_NOTE if any_partial else "")
        return {
            "seat": seat,
            "counts": counts,
            "items": shown,
            "window": window,
            "mark_read": mark_read,
            "note": note,
        }

    if conn is not None:
        return _build(conn)
    with connect() as db:
        return _build(db)
