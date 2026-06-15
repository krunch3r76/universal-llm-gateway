"""Turn CRUD operations."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..recipients import recipient_in_clause
from .connection import connect, now
from .threads import _next_auto_id


class SlugExists(Exception):  # noqa: N818
    """Raised when strict_slug=True and the requested slug is already taken."""

    def __init__(self, slug: str, existing_thread_id: str) -> None:
        self.slug = slug
        self.existing_thread_id = existing_thread_id
        super().__init__(
            f"Slug {slug!r} already exists on thread {existing_thread_id}"
        )


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
            "SELECT id, status, bus_lifecycle_state FROM threads WHERE id = ?",
            (thread,),
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
            # Replying to a closed thread reopens it — matches session-close protocol.
            conn.execute(
                "UPDATE threads SET status = 'active', updated_at = ? WHERE id = ?",
                (ts, thread),
            )
            # Advance lifecycle state when the closed thread is lifecycle-managed
            # and currently in a terminal state.
            from .lifecycle import TERMINAL_STATES, _transition_lifecycle_state

            lifecycle = row["bus_lifecycle_state"]
            if lifecycle in TERMINAL_STATES:
                _transition_lifecycle_state(conn, thread, "active", "reopen")
        else:
            # First turn posted to an admitted thread activates it — the delivery
            # path from Stargate hits this when it POSTs the pipeline result turn.
            lifecycle = row["bus_lifecycle_state"]
            if lifecycle == "admitted":
                from .lifecycle import _transition_lifecycle_state

                _transition_lifecycle_state(conn, thread, "active", "turn_posted")

        if supersedes_turn is not None:
            target = conn.execute(
                "SELECT id FROM turns WHERE id = ?", (supersedes_turn,)
            ).fetchone()
            if target is None:
                raise ValueError(f"supersedes_turn {supersedes_turn} does not exist")

        if after_turn is not None:
            # Mirror get_turns inbox semantics (legacy short to_agent slugs included).
            include_team = from_agent != "kaywan"
            inbox_clause, inbox_params = recipient_in_clause(
                from_agent, include_team=include_team
            )
            unread_rows = conn.execute(
                f"SELECT id, turn_number, subject FROM turns "
                f"WHERE thread = ? AND {inbox_clause} "
                f"AND turn_number > ? AND read_at IS NULL",
                (thread, *inbox_params, after_turn),
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
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    # include_superseded default mirrors the HTTP route's Query(False) — the route
    # is the single source of truth for this contract (F5). Superseded turns are
    # excluded from the normal fetch/mark-read path; mark-closed-read passes
    # include_superseded=True explicitly to sweep them on thread close.
    select = "id, thread, turn_number, from_agent, to_agent, subject, status, supersedes_turn, created_at, read_at"
    if not compact:
        select = "*"

    clauses: list[str] = []
    params: list[Any] = []

    if thread is not None:
        clauses.append("thread = ?")
        params.append(thread)
    if to is not None:
        # kaywan is a human seat — only sees 'all' broadcasts, not 'team'.
        # Canonical seat slugs must match legacy stored to_agent values (web, cursor).
        inbox_clause, inbox_params = recipient_in_clause(
            to, include_team=to != "kaywan"
        )
        clauses.append(inbox_clause)
        params.extend(inbox_params)
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
    close_candidates: set[str] = set()
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
                close_candidates = {
                    r["thread"] for r in rows if r["id"] in unread_ids
                }

        if compact:
            for row in rows:
                row["body"] = None
                row["attachments"] = None
        else:
            turn_ids = [r["id"] for r in rows]
            att_map = _get_attachments_for_turns(conn, turn_ids)
            for row in rows:
                row["attachments"] = att_map.get(row["id"])

    for affected in close_candidates:
        _maybe_close_generate_thread_on_read(affected)
    return rows


def get_unread_thread_toc(
    *,
    to: str,
    mark_read: bool = False,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Recipient-scoped unread inbox digest — one row per thread with unread
    turns addressed to ``to``.

    Mirrors get_turns' recipient/unread/non-superseded filter
    (recipient_in_clause + read_at IS NULL + status != 'superseded') so each
    row's unread_count reflects turns THIS seat must read — not the
    thread-global unread_count carried on ThreadDetail.

    The result is bounded by thread count (O(threads)) and each row is sparse
    (thread id, recipient-scoped unread_count, head turn_number) — descriptive
    fields are omitted so the digest stays small even across hundreds of
    threads (friction 16835: the flat List[Turn] form overflowed at routine
    fan-out). Agents expand a thread on demand via fetch_unread(thread=N),
    get(thread, turn_number), or threads(has_unread=true).

    When ``mark_read`` is True, every matching unread turn is marked read in the
    same transaction (preserves the fetch_unread(to=…, mark_read=true)
    "clear all" contract relied on by the 409 unread_turns_exist remediation).

    Returns ``(rows, marked_read_count)``; marked_read_count is 0 unless
    mark_read is True.
    """
    inbox_clause, inbox_params = recipient_in_clause(to, include_team=to != "kaywan")
    where = f"{inbox_clause} AND turns.read_at IS NULL AND turns.status != 'superseded'"
    limit_clause = f"LIMIT {int(limit)}" if limit is not None else ""
    # Sparse rows: thread id + recipient-scoped unread_count + head turn number.
    # Descriptive fields (slug, subject, participants) are intentionally omitted
    # so the digest stays bounded across hundreds of threads — agents expand a
    # thread on demand (fetch_unread(thread=N) / get / threads). Ordered by most
    # recent unread turn.
    select_sql = f"""
        SELECT
            turns.thread AS thread,
            COUNT(*) AS unread_count,
            MAX(turns.turn_number) AS latest_turn_number
        FROM turns
        WHERE {where}
        GROUP BY turns.thread
        ORDER BY MAX(turns.created_at) DESC
        {limit_clause}
    """
    close_candidates: list[str] = []
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(select_sql, inbox_params).fetchall()]
        marked = 0
        if mark_read:
            ts = now()
            cur = conn.execute(
                f"UPDATE turns SET read_at = ? WHERE {where}",
                [ts, *inbox_params],
            )
            marked = max(cur.rowcount, 0)
            if marked:
                close_candidates = [str(thread_row["thread"]) for thread_row in rows]
        result = rows, marked
    for thread_id in close_candidates:
        _maybe_close_generate_thread_on_read(thread_id)
    return result


def mark_turn_read(turn_id: int) -> str | None:
    """Returns read_at timestamp, or None if turn not found."""
    thread_id: str | None = None
    with connect() as conn:
        row = conn.execute(
            "SELECT read_at, thread FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()
        if row is None:
            return None
        if row["read_at"] is not None:
            return row["read_at"]
        thread_id = row["thread"]
        ts = now()
        conn.execute("UPDATE turns SET read_at = ? WHERE id = ?", (ts, turn_id))

    if thread_id is not None:
        _maybe_close_generate_thread_on_read(thread_id)
    return ts


def _maybe_close_generate_thread_on_read(thread_id: str) -> None:
    try:
        from ..close_on_read import maybe_close_generate_thread_on_read

        maybe_close_generate_thread_on_read(thread_id)
    except Exception:
        from universal_logging import get_logger

        get_logger(__name__).warning(
            "close-on-read hook failed: thread=%s",
            thread_id,
            exc_info=True,
        )


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


def create_turn(
    *,
    thread_id: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    body: str,
    status: str = "open",
    after_turn: int | None = None,
    attachments: list[dict[str, Any]] | None = None,
    close: bool = False,
    mark_read: bool = False,
) -> tuple[dict[str, Any], int, str, int]:
    """Post a turn to an existing thread for POST /threads/send continue path.

    Raises KeyError when thread_id does not exist.
    """
    from .threads import get_thread_with_links, normalize_thread_id
    from .threads_atomic import close_thread

    thread_id = normalize_thread_id(thread_id)
    if get_thread_with_links(thread_id) is None:
        raise KeyError(thread_id)

    effective_after = after_turn if after_turn and after_turn > 0 else None
    turn_id, ts, turn_number = insert_turn(
        thread=thread_id,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body=body,
        status=status,
        after_turn=effective_after,
        attachments=attachments,
    )

    if mark_read:
        mark_turn_read(turn_id)

    if close:
        close_thread(thread_id, mark_all_read=True)

    thread_row = get_thread_with_links(thread_id)
    if thread_row is None:
        raise KeyError(thread_id)
    return thread_row, turn_id, ts, turn_number
