"""Thread CRUD operations and auto-ID management."""

from __future__ import annotations

import sqlite3
from typing import Any

from .connection import connect, now


class ThreadHasReadTurns(Exception):  # noqa: N818
    """Raised when attempting to delete a thread that has read turns without force."""

    def __init__(self, read_count: int) -> None:
        self.read_count = read_count
        super().__init__(f"Thread has {read_count} read turn(s) - use force=True")


def normalize_thread_id(raw: str) -> str:
    """Resolve a numeric thread ID to its stored form.

    Tries exact match first, then searches for any zero-padded variant
    (e.g. "37" matches "037"). Non-numeric IDs are returned unchanged.
    """
    if not raw.isdigit():
        return raw
    with connect() as conn:
        exact = conn.execute("SELECT id FROM threads WHERE id = ?", (raw,)).fetchone()
        if exact is not None:
            return exact["id"]
        padded = conn.execute(
            "SELECT id FROM threads WHERE CAST(id AS INTEGER) = ? "
            "AND id GLOB '[0-9]*' LIMIT 1",
            (int(raw),),
        ).fetchone()
        if padded is not None:
            return padded["id"]
    return raw


def _thread_detail_sql() -> str:
    return """\
    SELECT
        t.id, t.slug, t.status, t.summary, t.created_at, t.updated_at,
        COUNT(tu.id)                                        AS turn_count,
        SUM(CASE WHEN tu.read_at IS NULL THEN 1 ELSE 0 END) AS unread_count,
        (SELECT subject FROM turns
         WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1) AS last_subject,
        (SELECT from_agent FROM turns
         WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1) AS last_turn_from,
        (SELECT to_agent FROM turns
         WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1) AS last_turn_to
    FROM threads t
    LEFT JOIN turns tu ON tu.thread = t.id
    """


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """Canonicalize a tag list: strip + lowercase + drop empties + dedupe.

    Applied identically on every write path (set_thread_tags) and every
    read-filter path (list_threads_v2 tag filter) so `Project:ULG` and
    `project:ulg` are the same tag and queries never silently miss.
    Order-preserving on first occurrence.
    """
    if not tags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw).strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def _load_thread_tags(
    conn: sqlite3.Connection, thread_ids: list[str]
) -> dict[str, list[str]]:
    """Return {thread_id: [tag, ...]} for the given ids, tags sorted asc."""
    if not thread_ids:
        return {}
    placeholders = ",".join("?" * len(thread_ids))
    rows = conn.execute(
        f"SELECT thread_id, tag FROM thread_tags "
        f"WHERE thread_id IN ({placeholders}) "
        f"ORDER BY thread_id, tag",
        thread_ids,
    ).fetchall()
    out: dict[str, list[str]] = {tid: [] for tid in thread_ids}
    for row in rows:
        out[row["thread_id"]].append(row["tag"])
    return out


def set_thread_tags(conn: sqlite3.Connection, thread_id: str, tags: list[str]) -> None:
    """Replace the full tag set for a thread. Pass [] to clear.

    Tags are normalized via _normalize_tags (strip + lowercase + dedupe) before
    write. Caller must hold the connection in an active transaction.
    """
    cleaned = _normalize_tags(tags)
    conn.execute("DELETE FROM thread_tags WHERE thread_id = ?", (thread_id,))
    if cleaned:
        conn.executemany(
            "INSERT INTO thread_tags (thread_id, tag) VALUES (?, ?)",
            [(thread_id, t) for t in cleaned],
        )


def list_threads_v2(
    *, status: str | None = None, tags: list[str] | None = None
) -> list[dict[str, Any]]:
    """List threads with optional status + AND-tag filter.

    `tags`: threads must have ALL listed tags. None or [] = no tag filter.
    """
    base = _thread_detail_sql()
    params: list[Any] = []
    wheres: list[str] = []
    if status is not None:
        wheres.append("t.status = ?")
        params.append(status)
    tag_list = _normalize_tags(tags)
    if tag_list:
        # AND match: join thread_tags and require all N tags matched.
        placeholders = ",".join("?" * len(tag_list))
        wheres.append(
            f"t.id IN ("
            f"  SELECT thread_id FROM thread_tags "
            f"  WHERE tag IN ({placeholders}) "
            f"  GROUP BY thread_id HAVING COUNT(DISTINCT tag) = ?"
            f")"
        )
        params.extend(tag_list)
        params.append(len(tag_list))
    where_clause = f"WHERE {' AND '.join(wheres)}" if wheres else ""
    sql = f"{base} {where_clause} GROUP BY t.id ORDER BY t.updated_at DESC"
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        tag_map = _load_thread_tags(conn, [r["id"] for r in rows])
        for row in rows:
            row["tags"] = tag_map.get(row["id"], [])
        return rows


def get_thread(thread_id: str) -> dict[str, Any] | None:
    sql = f"{_thread_detail_sql()} WHERE t.id = ? GROUP BY t.id"
    with connect() as conn:
        row = conn.execute(sql, (thread_id,)).fetchone()
        if row is None:
            return None
        detail = dict(row)
        detail["tags"] = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        return detail


def get_thread_summary(thread_id: str, *, recent: int = 3) -> dict[str, Any] | None:
    thread = get_thread(thread_id)
    if thread is None:
        return None
    with connect() as conn:
        subjects = [
            row["subject"]
            for row in conn.execute(
                "SELECT subject FROM turns WHERE thread = ? "
                "ORDER BY turn_number DESC LIMIT ?",
                (thread_id, recent),
            ).fetchall()
        ]
    thread["recent_subjects"] = subjects
    return thread


def get_thread_turns_asc(thread_id: str) -> list[dict[str, Any]]:
    """All turns for a thread ordered by turn_number ASC, with attachments."""
    from .turns import _get_attachments_for_turns

    with connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM turns WHERE thread = ? ORDER BY turn_number ASC",
                (thread_id,),
            ).fetchall()
        ]
        turn_ids = [r["id"] for r in rows]
        att_map = _get_attachments_for_turns(conn, turn_ids)
        for row in rows:
            row["attachments"] = att_map.get(row["id"])
        return rows


def _seed_auto_id(conn: sqlite3.Connection) -> str:
    """Compute initial auto-ID from existing threads (one-time migration)."""
    row = conn.execute(
        "SELECT id FROM threads WHERE id GLOB '[0-9]*' "
        "ORDER BY CAST(id AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "001"
    width = len(row["id"])
    return str(int(row["id"]) + 1).zfill(width)


def _next_auto_id(conn: sqlite3.Connection) -> str:
    """Return next available numeric thread ID using an existing connection.

    Uses a persistent counter in thread_meta so explicit out-of-sequence
    IDs (e.g. "999") don't pollute the auto-assigned sequence. Skips past
    any IDs that already exist (self-heals if counter is stale).
    """
    row = conn.execute(
        "SELECT value FROM thread_meta WHERE key = 'next_auto_id'"
    ).fetchone()
    candidate = row["value"] if row is not None else _seed_auto_id(conn)
    width = len(candidate)
    while (
        conn.execute("SELECT 1 FROM threads WHERE id = ?", (candidate,)).fetchone()
        is not None
    ):
        candidate = str(int(candidate) + 1).zfill(width)
    after = str(int(candidate) + 1).zfill(width)
    conn.execute(
        "INSERT OR REPLACE INTO thread_meta (key, value) VALUES ('next_auto_id', ?)",
        (after,),
    )
    return candidate


def next_thread_id() -> str:
    """Public wrapper - allocates a connection for standalone callers."""
    with connect() as conn:
        return _next_auto_id(conn)


def create_thread(
    *,
    thread_id: str | None,
    slug: str,
    summary: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Returns thread detail, or None if thread_id was supplied and already exists."""
    if thread_id is None:
        thread_id = next_thread_id()
    ts = now()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if existing is not None:
            return None
        conn.execute(
            "INSERT INTO threads (id, slug, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, slug, summary, ts, ts),
        )
        if tags:
            set_thread_tags(conn, thread_id, tags)
    thread_detail = get_thread(thread_id)
    if thread_detail is None:
        raise RuntimeError(f"Failed to fetch newly created thread {thread_id}")
    return thread_detail


def rename_thread(old_id: str, new_id: str) -> dict[str, Any] | None:
    """Change a thread's ID, re-pointing all turns, legacy messages, and tags.

    Returns updated thread detail, or None if old_id not found.
    Raises ValueError if new_id already exists.
    """
    with connect() as conn:
        old = conn.execute("SELECT * FROM threads WHERE id = ?", (old_id,)).fetchone()
        if old is None:
            return None

        if (
            conn.execute("SELECT id FROM threads WHERE id = ?", (new_id,)).fetchone()
            is not None
        ):
            raise ValueError(f"Thread {new_id} already exists")

        ts = now()
        conn.execute(
            "INSERT INTO threads (id, slug, status, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id, old["slug"], old["status"], old["summary"], old["created_at"], ts),
        )
        conn.execute("UPDATE turns SET thread = ? WHERE thread = ?", (new_id, old_id))
        conn.execute(
            "UPDATE messages SET thread = ? WHERE thread = ?", (new_id, old_id)
        )
        # Re-point tags before deleting the old thread row so ON DELETE CASCADE
        # doesn't drop them.
        conn.execute(
            "UPDATE thread_tags SET thread_id = ? WHERE thread_id = ?",
            (new_id, old_id),
        )
        conn.execute("DELETE FROM threads WHERE id = ?", (old_id,))
    return get_thread(new_id)


def update_thread(
    thread_id: str,
    *,
    status: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any] | None:
    """Returns updated thread detail, or None if not found.

    `tags`: None = leave existing unchanged. [] = clear all. [...] = replace.

    Invariant: status transition → 'closed' also marks every unread turn read,
    matching the dedicated /close route. Closing a thread clears its unread
    queue regardless of which endpoint is used.
    """
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [ts]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if summary is not None:
            sets.append("summary = ?")
            params.append(summary)
        params.append(thread_id)
        conn.execute(f"UPDATE threads SET {', '.join(sets)} WHERE id = ?", params)
        if status == "closed":
            conn.execute(
                "UPDATE turns SET read_at = ? WHERE thread = ? AND read_at IS NULL",
                (ts, thread_id),
            )
        if tags is not None:
            set_thread_tags(conn, thread_id, tags)
    return get_thread(thread_id)


def delete_thread(thread_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a thread and all its turns.

    If force=False, refuses to delete threads that have any read turns.
    Returns {"deleted_turns": <count>, "thread": "<id>"}.
    Raises KeyError if thread not found.
    Raises ThreadHasReadTurns if force=False and any turns have been read.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Thread {thread_id} not found")

        if not force:
            read_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM turns "
                "WHERE thread = ? AND read_at IS NOT NULL",
                (thread_id,),
            ).fetchone()
            if read_row["cnt"] > 0:
                raise ThreadHasReadTurns(read_row["cnt"])

        count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM turns WHERE thread = ?",
            (thread_id,),
        ).fetchone()
        deleted_turns = count_row["cnt"]

        conn.execute("DELETE FROM turns WHERE thread = ?", (thread_id,))
        conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))

    return {"deleted_turns": deleted_turns, "thread": thread_id}
