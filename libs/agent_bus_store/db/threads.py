"""Thread CRUD operations and auto-ID management."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from agent_seat.registry import expand_recipient_slugs

from ..turns_models import (
    _BROADCAST_TO_AGENTS,
    TRIAGE_CONFIRM_TTL_SECONDS,
)
from .connection import connect, now

_QUERY_MAX_LEN = 200


def _escape_like_literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_substring_pattern(substring: str) -> str:
    return f"%{_escape_like_literal(substring)}%"


def _normalize_query(query: str | None) -> str | None:
    """Strip, clamp to 200 chars, return None when empty."""
    if query is None:
        return None
    cleaned = query.strip()
    if not cleaned:
        return None
    return cleaned[:_QUERY_MAX_LEN]


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
        t.bus_lifecycle_state,
        COUNT(tu.id)                                        AS turn_count,
        COALESCE(
            SUM(CASE WHEN tu.read_at IS NULL THEN 1 ELSE 0 END), 0
        )                                                   AS unread_count,
        (SELECT subject FROM turns
         WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1) AS last_subject,
        (SELECT from_agent FROM turns
         WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1) AS last_turn_from,
        (SELECT to_agent FROM turns
         WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1) AS last_turn_to
    FROM threads t
    LEFT JOIN turns tu ON tu.thread = t.id
    """


def _load_dispatch_links(
    conn: sqlite3.Connection, thread_id: str
) -> list[dict[str, Any]]:
    """Return dispatch link summaries for a single thread."""
    rows = conn.execute(
        "SELECT execution_id, pipeline_id, linked_at, terminal_status, delivery_at "
        "FROM thread_dispatch_links WHERE thread_id = ? ORDER BY linked_at ASC",
        (thread_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_dispatch_link_by_execution_id(
    execution_id: str,
) -> dict[str, Any] | None:
    """Resolve a dispatch link row by execution_id (indexed lookup)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT thread_id, pipeline_id, terminal_status, terminal_at "
            "FROM thread_dispatch_links WHERE execution_id = ? LIMIT 1",
            (execution_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


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


def add_thread_tags(conn: sqlite3.Connection, thread_id: str, tags: list[str]) -> None:
    """Add tags without removing unspecified existing tags (single transaction)."""
    if not tags:
        return
    existing = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
    merged = _normalize_tags([*existing, *tags])
    set_thread_tags(conn, thread_id, merged)


def remove_thread_tags(
    conn: sqlite3.Connection, thread_id: str, tags: list[str]
) -> None:
    """Remove listed tags only; unspecified tags are preserved."""
    if not tags:
        return
    remove_set = set(_normalize_tags(tags))
    if not remove_set:
        return
    existing = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
    remaining = [t for t in existing if t not in remove_set]
    set_thread_tags(conn, thread_id, remaining)


def add_tags(
    thread_id: str,
    tags: list[str],
    *,
    enroll_charter_runner: bool = False,
) -> dict[str, Any] | None:
    """Add tags to a thread without clobbering unspecified tags."""
    from agent_bus_store.thread_classification import gate_thread_tags

    if not tags:
        return get_thread(thread_id)
    ts = now()
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        prior_tags = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        gated = gate_thread_tags(
            tags,
            prior_tags=prior_tags,
            enroll_charter_runner=enroll_charter_runner,
        )
        add_thread_tags(conn, thread_id, gated)
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (ts, thread_id))
    return get_thread(thread_id)


def remove_tags(thread_id: str, tags: list[str]) -> dict[str, Any] | None:
    """Remove listed tags from a thread; other tags remain.

    Stripping ``charter-runner`` from an already-closed root emits
    ``manage.charter.tick.root_closed`` (same authority as ``update_thread``).
    """
    if not tags:
        return get_thread(thread_id)
    from ..events.thread_closed import maybe_emit_charter_root_closed_on_unenroll

    ts = now()
    prior_tags: list[str] = []
    prior_status = ""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        prior_status = str(row["status"] or "")
        prior_tags = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        remove_thread_tags(conn, thread_id, tags)
        conn.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (ts, thread_id))
    detail = get_thread(thread_id)
    if detail is None:
        return None
    maybe_emit_charter_root_closed_on_unenroll(
        root=thread_id,
        prior_tags=prior_tags,
        new_tags=list(detail.get("tags") or []),
        status=str(detail.get("status") or prior_status),
        reason="unenroll_after_close",
    )
    return detail


def list_threads_v2(
    *,
    status: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    has_unread: bool | None = None,
    limit: int | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    """List threads with optional status + lifecycle_state + AND-tag filter.

    `tags`: threads must have ALL listed tags. None or [] = no tag filter.
    `lifecycle_state`: exact match on bus_lifecycle_state. None = no filter.
    `has_unread`: when True, only return threads with at least one unread turn.
        When False, only return threads with zero unread turns. None = no
        filter (default; preserves prior behaviour).
    `limit`: cap result count. None = no cap. Applied after ORDER BY so the
        most recently updated threads are returned first.
    `query`: case-insensitive substring over slug, summary, and last_subject.
        Clamped to 200 chars; empty/whitespace-only is treated as no filter.
    """
    base = _thread_detail_sql()
    params: list[Any] = []
    wheres: list[str] = []
    if status is not None:
        wheres.append("t.status = ?")
        params.append(status)
    if lifecycle_state is not None:
        wheres.append("t.bus_lifecycle_state = ?")
        params.append(lifecycle_state)
    normalized_query = _normalize_query(query)
    if normalized_query is not None:
        pattern = _like_substring_pattern(normalized_query.lower())
        wheres.append(
            "("
            "lower(t.slug) LIKE ? ESCAPE '\\' OR "
            "lower(coalesce(t.summary, '')) LIKE ? ESCAPE '\\' OR "
            "lower(coalesce(("
            "  SELECT subject FROM turns "
            "  WHERE thread = t.id ORDER BY turn_number DESC LIMIT 1"
            "), '')) LIKE ? ESCAPE '\\'"
            ")"
        )
        params.extend([pattern, pattern, pattern])
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
    # has_unread filters on the aggregate unread_count, so it goes in HAVING
    # (post-aggregation) rather than WHERE.
    having_clause = ""
    if has_unread is True:
        having_clause = "HAVING unread_count > 0"
    elif has_unread is False:
        having_clause = "HAVING unread_count = 0"
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(int(limit))
    sql = (
        f"{base} {where_clause} GROUP BY t.id "
        f"{having_clause} ORDER BY t.updated_at DESC {limit_clause}"
    )
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        tag_map = _load_thread_tags(conn, [r["id"] for r in rows])
        for row in rows:
            row["tags"] = tag_map.get(row["id"], [])
        return rows


def get_thread(thread_id: str) -> dict[str, Any] | None:
    """Fetch thread detail without dispatch_links (used for non-lifecycle paths)."""
    sql = f"{_thread_detail_sql()} WHERE t.id = ? GROUP BY t.id"
    with connect() as conn:
        row = conn.execute(sql, (thread_id,)).fetchone()
        if row is None:
            return None
        detail = dict(row)
        detail["tags"] = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        detail.setdefault("dispatch_links", [])
        return detail


def get_thread_with_links(thread_id: str) -> dict[str, Any] | None:
    """Fetch thread detail including dispatch_links (for lifecycle-aware paths)."""
    sql = f"{_thread_detail_sql()} WHERE t.id = ? GROUP BY t.id"
    with connect() as conn:
        row = conn.execute(sql, (thread_id,)).fetchone()
        if row is None:
            return None
        detail = dict(row)
        detail["tags"] = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        detail["dispatch_links"] = _load_dispatch_links(conn, thread_id)
        return detail


def get_thread_summary(thread_id: str, *, recent: int = 3) -> dict[str, Any] | None:
    """Return thread metadata plus the ``recent`` most recent turn subjects, or None."""
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
    lifecycle_state: str | None = None,
    enroll_charter_runner: bool = False,
) -> dict[str, Any] | None:
    """Returns thread detail with dispatch_links, or None if thread_id already exists."""
    from agent_bus_store.thread_classification import gate_thread_tags

    from .lifecycle import _transition_lifecycle_state

    gated_tags = gate_thread_tags(
        tags, prior_tags=[], enroll_charter_runner=enroll_charter_runner
    )
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
        if gated_tags:
            set_thread_tags(conn, thread_id, gated_tags)
        if lifecycle_state is not None:
            _transition_lifecycle_state(conn, thread_id, lifecycle_state, "create")
    thread_detail = get_thread_with_links(thread_id)
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
    enroll_charter_runner: bool = False,
) -> dict[str, Any] | None:
    """Returns updated thread detail, or None if not found.

    `tags`: None = leave existing unchanged. [] = clear all. [...] = replace.

    Invariant: status transition → 'closed' also marks every unread turn read,
    matching the dedicated /close route. Closing a thread clears its unread
    queue regardless of which endpoint is used.
    """
    from agent_bus_store.enrollment_guard import ENROLLMENT_TAG
    from agent_bus_store.thread_classification import gate_thread_tags

    from ..events.thread_closed import (
        emit_thread_closed,
        maybe_emit_charter_root_closed_on_unenroll,
    )

    ts = now()
    prior_status: str | None = None
    prior_tags: list[str] = []
    with connect() as conn:
        row = conn.execute(
            "SELECT id, status FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            return None
        prior_status = str(row["status"] or "")
        prior_tags = _load_thread_tags(conn, [thread_id]).get(thread_id, [])
        if tags is not None:
            tags = gate_thread_tags(
                tags,
                prior_tags=prior_tags,
                enroll_charter_runner=enroll_charter_runner,
            )
        # Closing an enrolled root without an explicit tags replace: strip
        # enrollment so seat/update_thread(status=closed) cannot leave a parked
        # board row with charter-runner still attached.
        if (
            status == "closed"
            and prior_status != "closed"
            and tags is None
            and ENROLLMENT_TAG in prior_tags
        ):
            tags = [t for t in prior_tags if t != ENROLLMENT_TAG]
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
    detail = get_thread(thread_id)
    if detail is None:
        return None
    if status == "closed" and prior_status != "closed":
        emit_thread_closed(thread_id, via="update_thread")
    maybe_emit_charter_root_closed_on_unenroll(
        root=thread_id,
        prior_tags=prior_tags,
        new_tags=list(detail.get("tags") or prior_tags),
        status=str(detail.get("status") or ""),
    )
    return detail


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


def _triage_owned_recipients(agent: str) -> list[str]:
    return [
        slug
        for slug in expand_recipient_slugs(agent)
        if slug not in _BROADCAST_TO_AGENTS
    ]


def _triage_filter_hash(
    *,
    agent: str,
    action: str,
    older_than: str,
    status: str | None,
    candidate_ids: list[str],
) -> str:
    payload = {
        "agent": agent,
        "action": action,
        "older_than": older_than,
        "status": status,
        "candidate_ids": sorted(candidate_ids),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class _TriageConfirmEntry:
    token_id: str
    agent: str
    action: str
    older_than: str
    status: str | None
    candidate_ids: list[str]
    filter_hash: str
    expires_at: datetime
    used: bool = False


_triage_tokens: dict[str, _TriageConfirmEntry] = {}
_triage_tokens_lock = threading.Lock()


def _prune_expired_triage_tokens(now_ts: datetime) -> None:
    expired = [
        token_id
        for token_id, entry in _triage_tokens.items()
        if entry.expires_at <= now_ts or entry.used
    ]
    for token_id in expired:
        _triage_tokens.pop(token_id, None)


def issue_triage_confirm_token(
    *,
    agent: str,
    action: str,
    older_than: str,
    status: str | None,
    candidate_ids: list[str],
) -> tuple[str, datetime]:
    """Mint a single-use confirm token bound to filter + candidate set."""
    token_id = uuid.uuid4().hex
    expires_at = datetime.now(UTC) + timedelta(seconds=TRIAGE_CONFIRM_TTL_SECONDS)
    filter_hash = _triage_filter_hash(
        agent=agent,
        action=action,
        older_than=older_than,
        status=status,
        candidate_ids=candidate_ids,
    )
    with _triage_tokens_lock:
        _prune_expired_triage_tokens(datetime.now(UTC))
        _triage_tokens[token_id] = _TriageConfirmEntry(
            token_id=token_id,
            agent=agent,
            action=action,
            older_than=older_than,
            status=status,
            candidate_ids=list(candidate_ids),
            filter_hash=filter_hash,
            expires_at=expires_at,
        )
    return token_id, expires_at


def consume_triage_confirm_token(
    *,
    token_id: str,
    agent: str,
    action: str,
    older_than: str,
    status: str | None,
    candidate_ids: list[str],
) -> Literal["ok", "invalid", "expired", "filter_mismatch"]:
    """Validate and consume a confirm token (single-use)."""
    filter_hash = _triage_filter_hash(
        agent=agent,
        action=action,
        older_than=older_than,
        status=status,
        candidate_ids=candidate_ids,
    )
    now_ts = datetime.now(UTC)
    with _triage_tokens_lock:
        entry = _triage_tokens.get(token_id)
        if entry is None:
            return "invalid"
        if entry.expires_at <= now_ts:
            _triage_tokens.pop(token_id, None)
            return "expired"
        if entry.used:
            return "invalid"
        if (
            entry.agent != agent
            or entry.action != action
            or entry.older_than != older_than
            or entry.status != status
            or entry.filter_hash != filter_hash
        ):
            return "filter_mismatch"
        entry.used = True
        _triage_tokens.pop(token_id, None)
    return "ok"


def list_triage_candidates(
    *,
    agent: str,
    activity_cutoff: datetime,
    action: str,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return owned unread threads older than cutoff (full set before cap)."""
    _ = action  # floors enforced at route layer; action reserved for filter binding
    owned = _triage_owned_recipients(agent)
    if not owned:
        return [], 0
    owned_placeholders = ",".join("?" * len(owned))
    params: list[Any] = []
    status_clause = ""
    if status is not None:
        status_clause = "AND t.status = ?"
        params.append(status)

    sql = f"""
        SELECT
            t.id AS id,
            t.slug AS slug,
            t.status AS status,
            t.bus_lifecycle_state AS bus_lifecycle_state,
            MAX(turns.created_at) AS last_activity_at,
            COUNT(*) AS unread_count
        FROM threads t
        INNER JOIN turns ON turns.thread = t.id
        WHERE turns.read_at IS NULL
          AND turns.status != 'superseded'
          AND t.status NOT IN ('blocked')
          AND (t.bus_lifecycle_state IS NULL
               OR t.bus_lifecycle_state NOT IN ('pending', 'admitted'))
          {status_clause}
        GROUP BY t.id
        HAVING MAX(turns.created_at) <= ?
           AND SUM(CASE WHEN turns.to_agent NOT IN ({owned_placeholders})
                        OR turns.to_agent IN ('all', 'team')
                   THEN 1 ELSE 0 END) = 0
           AND SUM(CASE WHEN turns.to_agent IN ({owned_placeholders})
                   THEN 1 ELSE 0 END) > 0
        ORDER BY last_activity_at ASC
    """
    cutoff_iso = activity_cutoff.isoformat()
    query_params = [*params, cutoff_iso, *owned, *owned]
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, query_params).fetchall()]
    return rows, len(rows)


def execute_triage_mark_read(*, agent: str, thread_ids: list[str]) -> int:
    """Mark caller-owned unread turns read (skips broadcast recipients)."""
    owned = _triage_owned_recipients(agent)
    if not owned or not thread_ids:
        return 0
    owned_placeholders = ",".join("?" * len(owned))
    thread_placeholders = ",".join("?" * len(thread_ids))
    ts = now()
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE turns SET read_at = ? "
            f"WHERE thread IN ({thread_placeholders}) "
            f"AND to_agent IN ({owned_placeholders}) "
            f"AND read_at IS NULL AND status != 'superseded'",
            [ts, *thread_ids, *owned],
        )
        marked = max(cur.rowcount, 0)
    return marked


def execute_triage_close(*, thread_ids: list[str]) -> int:
    """Close triage candidate threads (all unread are caller-owned)."""
    from .threads_atomic import close_thread

    closed = 0
    for thread_id in thread_ids:
        row = close_thread(thread_id, mark_all_read=True)
        if row is not None:
            closed += 1
    return closed
