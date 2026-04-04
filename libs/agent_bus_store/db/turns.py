"""Turn CRUD operations."""

from __future__ import annotations

import sqlite3
from typing import Any

from .connection import connect, now
from .threads import _next_auto_id


class UnreadTurnsExist(Exception):  # noqa: N818
    """Raised when after_turn check finds unread turns addressed to the poster."""

    def __init__(self, unread: list[dict[str, Any]]) -> None:
        self.unread = unread
        super().__init__(f"{len(unread)} unread turn(s) exist")


class TurnAlreadyAcknowledged(Exception):  # noqa: N818
    """Raised when attempting to modify a turn whose read_at is already set."""


# ── Attachment helpers ───────────────────────────────────────────────


def _insert_attachments(
    conn: sqlite3.Connection,
    turn_id: int,
    attachments: list[dict[str, Any]],
) -> None:
    """Insert attachment metadata rows for a turn within an existing transaction."""
    for att in attachments:
        conn.execute(
            "INSERT INTO turn_attachments "
            "(turn_id, filename, path, mime_type, size_bytes, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                turn_id,
                att["filename"],
                att["path"],
                att.get("mime_type"),
                att.get("size_bytes"),
                att.get("sha256"),
            ),
        )


def _get_attachments_for_turns(
    conn: sqlite3.Connection, turn_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    """Fetch attachments grouped by turn_id."""
    if not turn_ids:
        return {}
    placeholders = ",".join("?" for _ in turn_ids)
    rows = conn.execute(
        f"SELECT * FROM turn_attachments WHERE turn_id IN ({placeholders})",
        turn_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        grouped.setdefault(d["turn_id"], []).append(d)
    return grouped


# ── Turn CRUD ────────────────────────────────────────────────────────


def insert_turn(
    *,
    thread: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    status: str = "open",
    thread_slug: str | None = None,
    after_turn: int | None = None,
    supersedes_turn: int | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> tuple[int, str, int]:
    """Returns (turn_id, created_at, turn_number).

    Raises UnreadTurnsExist if after_turn is provided and unread turns
    addressed to from_agent exist after that turn number.
    """
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status FROM threads WHERE id = ?", (thread,)
        ).fetchone()
        if row is None:
            slug = thread_slug or thread
            if thread.isdigit():
                actual_id = thread
            else:
                actual_id = _next_auto_id(conn)
            conn.execute(
                "INSERT INTO threads (id, slug, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (actual_id, slug, ts, ts),
            )
            thread = actual_id
        elif row["status"] == "closed":
            raise ValueError(f"Thread {thread} is closed - reopen before posting")

        if supersedes_turn is not None:
            target = conn.execute(
                "SELECT id FROM turns WHERE id = ?", (supersedes_turn,)
            ).fetchone()
            if target is None:
                raise ValueError(f"supersedes_turn {supersedes_turn} does not exist")

        if after_turn is not None:
            unread_rows = conn.execute(
                "SELECT id, turn_number, subject FROM turns "
                "WHERE thread = ? AND to_agent IN (?, 'all') "
                "AND turn_number > ? AND read_at IS NULL",
                (thread, from_agent, after_turn),
            ).fetchall()
            if unread_rows:
                raise UnreadTurnsExist([dict(r) for r in unread_rows])

        max_row = conn.execute(
            "SELECT MAX(turn_number) AS max_tn FROM turns WHERE thread = ?",
            (thread,),
        ).fetchone()
        turn_number = (max_row["max_tn"] or 0) + 1

        cur = conn.execute(
            "INSERT INTO turns "
            "(thread, turn_number, from_agent, to_agent, subject, body, "
            "status, supersedes_turn, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread,
                turn_number,
                from_agent,
                to_agent,
                subject,
                body,
                status,
                supersedes_turn,
                ts,
            ),
        )

        if supersedes_turn is not None:
            conn.execute(
                "UPDATE turns SET status = 'superseded' WHERE id = ?",
                (supersedes_turn,),
            )

        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (ts, thread))

        if cur.lastrowid is None:
            raise RuntimeError("Failed to insert turn: sqlite returned no row id")

        turn_id = cur.lastrowid
        if attachments:
            _insert_attachments(conn, turn_id, attachments)

        return turn_id, ts, turn_number


def get_turns(
    *,
    thread: str | None = None,
    to: str | None = None,
    unread: bool = False,
    status: str | None = None,
    last: int | None = None,
    compact: bool = False,
    mark_read: bool = False,
    include_superseded: bool = True,
) -> list[dict[str, Any]]:
    select = "id, thread, turn_number, from_agent, to_agent, subject, status, supersedes_turn, created_at, read_at"
    if not compact:
        select = "*"

    clauses: list[str] = []
    params: list[Any] = []

    if thread is not None:
        clauses.append("thread = ?")
        params.append(thread)
    if to is not None:
        clauses.append("(to_agent = ? OR to_agent = 'all')")
        params.append(to)
    if unread:
        clauses.append("read_at IS NULL")
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if not include_superseded:
        clauses.append("status != 'superseded'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "ORDER BY thread, turn_number DESC"
    limit = f"LIMIT {last}" if last is not None else ""

    sql = f"SELECT {select} FROM turns {where} {order} {limit}"
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]

        if mark_read:
            ts = now()
            unread_ids = [r["id"] for r in rows if r["read_at"] is None]
            if unread_ids:
                placeholders = ",".join("?" for _ in unread_ids)
                conn.execute(
                    f"UPDATE turns SET read_at = ? WHERE id IN ({placeholders})",
                    [ts, *unread_ids],
                )
                for row in rows:
                    if row["read_at"] is None:
                        row["read_at"] = ts

        if compact:
            for row in rows:
                row["body"] = None
                row["attachments"] = None
        else:
            turn_ids = [r["id"] for r in rows]
            att_map = _get_attachments_for_turns(conn, turn_ids)
            for row in rows:
                row["attachments"] = att_map.get(row["id"])

        return rows


def mark_turn_read(turn_id: int) -> str | None:
    """Returns read_at timestamp, or None if turn not found."""
    with connect() as conn:
        row = conn.execute(
            "SELECT read_at FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        if row is None:
            return None
        if row["read_at"] is not None:
            return row["read_at"]
        ts = now()
        conn.execute("UPDATE turns SET read_at = ? WHERE id = ?", (ts, turn_id))
        return ts


def update_turn_status(
    turn_id: int, *, status: str, supersedes_turn: int | None = None
) -> bool:
    """Returns False if turn not found."""
    with connect() as conn:
        row = conn.execute("SELECT id FROM turns WHERE id = ?", (turn_id,)).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE turns SET status = ?, supersedes_turn = ? WHERE id = ?",
            (status, supersedes_turn, turn_id),
        )
        return True


def update_turn(
    turn_id: int,
    *,
    subject: str | None = None,
    body: str | None = None,
    append: str | None = None,
) -> dict[str, Any] | None:
    """Update turn subject/body/append. Only permitted while read_at is null.

    ``body`` replaces the entire body; ``append`` concatenates to it.
    Providing both is an error.

    Returns updated turn dict, None if not found.
    Raises TurnAlreadyAcknowledged if read_at is set.
    """
    if body is not None and append is not None:
        raise ValueError("Cannot specify both body (replace) and append")

    with connect() as conn:
        row = conn.execute("SELECT * FROM turns WHERE id = ?", (turn_id,)).fetchone()
        if row is None:
            return None
        if row["read_at"] is not None:
            raise TurnAlreadyAcknowledged

        sets: list[str] = []
        params: list[Any] = []
        if subject is not None:
            sets.append("subject = ?")
            params.append(subject)
        if body is not None:
            sets.append("body = ?")
            params.append(body)
        if append is not None:
            sets.append("body = body || ?")
            params.append(append)
        if not sets:
            return dict(row)

        params.append(turn_id)
        conn.execute(f"UPDATE turns SET {', '.join(sets)} WHERE id = ?", params)
        updated = conn.execute(
            "SELECT * FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        return dict(updated)


def get_turn_by_number(thread: str, turn_number: int) -> dict[str, Any] | None:
    """Look up a single turn by thread + turn_number, including attachments."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM turns WHERE thread = ? AND turn_number = ?",
            (thread, turn_number),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        att_map = _get_attachments_for_turns(conn, [d["id"]])
        d["attachments"] = att_map.get(d["id"])
        return d


def delete_turn(turn_id: int, *, force: bool = False) -> dict[str, Any]:
    """Delete a single turn by ID.

    If force=False, refuses to delete turns that have been read (read_at set).
    Clears any supersedes_turn references pointing at this turn before deletion.
    Touches updated_at on the parent thread. Does NOT auto-delete an empty thread.

    Returns {"deleted_turn": <turn_id>, "thread": "<id>", "turn_number": <n>}.
    Raises KeyError if turn not found.
    Raises TurnAlreadyAcknowledged if force=False and turn has been read.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id, thread, turn_number, read_at FROM turns WHERE id = ?",
            (turn_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Turn {turn_id} not found")

        if not force and row["read_at"] is not None:
            raise TurnAlreadyAcknowledged

        thread_id = row["thread"]
        turn_number = row["turn_number"]

        conn.execute(
            "UPDATE turns SET supersedes_turn = NULL WHERE supersedes_turn = ?",
            (turn_id,),
        )
        conn.execute("DELETE FROM turns WHERE id = ?", (turn_id,))
        conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (now(), thread_id),
        )

    return {
        "deleted_turn": turn_id,
        "thread": thread_id,
        "turn_number": turn_number,
    }
