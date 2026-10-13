"""Sweep CDP pointer turns that never received a dispatch link (legacy / crash gap)."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from .cdp_pointer_body import (
    CDP_POINTER_BODY_PREFIX,
    format_cdp_generate_pointer_body,
    parse_cdp_pointer_execution_id,
)
from .db.connection import connect, now, write_connect
from .db.threads_atomic import _insert_dispatch_link_row

logger = get_logger(__name__)

CDP_POINTER_ORPHAN_GRACE_SECONDS = 300
CDP_POINTER_PIPELINE_ID = "cdp-generate"
CDP_POINTER_SWEEP_BATCH_LIMIT = 50
# Bound candidate scan cost (turn id lower bound). Orphans older than this
# window are never stamped by the sweep. Linked rows are excluded before LIMIT.
CDP_POINTER_SWEEP_ID_WINDOW = 5000

__all__ = [
    "CDP_POINTER_BODY_PREFIX",
    "format_cdp_generate_pointer_body",
    "parse_cdp_pointer_execution_id",
]


def _failed_subject(execution_id: str) -> str:
    return f"cdp FAILED — {execution_id[:8]}"


def _sweep_one_orphan(
    *,
    thread_id: str,
    execution_id: str,
    pointer_from_agent: str,
) -> bool:
    """Stamp failed link + one FAILED turn for a pointer-without-link orphan."""
    ts = now()
    short = execution_id[:8]
    failed_subject = _failed_subject(execution_id)
    failed_body = (
        f"CDP generate pointer orphaned (execution_id={execution_id}). "
        "No dispatch link was registered before the grace window expired."
    )

    with write_connect() as conn:
        link = conn.execute(
            "SELECT terminal_status FROM thread_dispatch_links "
            "WHERE thread_id = ? AND execution_id = ?",
            (thread_id, execution_id),
        ).fetchone()
        if link is not None:
            return False

        existing_failed = conn.execute(
            "SELECT 1 FROM turns WHERE thread = ? AND subject LIKE ? "
            "AND body LIKE ? LIMIT 1",
            (thread_id, "cdp FAILED%", f"%execution_id={execution_id}%"),
        ).fetchone()
        if existing_failed is not None:
            return False

        _insert_dispatch_link_row(
            conn,
            thread_id=thread_id,
            execution_id=execution_id,
            pipeline_id=CDP_POINTER_PIPELINE_ID,
            caller_agent=pointer_from_agent,
            linked_at=ts,
        )
        conn.execute(
            "UPDATE thread_dispatch_links "
            "SET terminal_status = ?, terminal_at = ?, delivery_at = ? "
            "WHERE thread_id = ? AND execution_id = ? AND terminal_status IS NULL",
            ("failed", ts, ts, thread_id, execution_id),
        )

        turn_row = conn.execute(
            "SELECT COALESCE(MAX(turn_number), 0) AS mx FROM turns WHERE thread = ?",
            (thread_id,),
        ).fetchone()
        turn_number = int(turn_row["mx"] or 0) + 1

        from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM

        conn.execute(
            "INSERT INTO turns "
            "(thread, turn_number, from_agent, to_agent, subject, body, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                turn_number,
                CDP_REPLY_FROM,
                pointer_from_agent or "dispatch",
                failed_subject,
                failed_body,
                "open",
                ts,
            ),
        )

    logger.info(
        "cdp_pointer_sweep: stamped failed link thread=%s execution_id=%s",
        thread_id,
        short,
    )
    return True


def sweep_cdp_pointer_orphans(
    *,
    grace_seconds: int = CDP_POINTER_ORPHAN_GRACE_SECONDS,
    batch_limit: int = CDP_POINTER_SWEEP_BATCH_LIMIT,
) -> int:
    """Reap pointer turns older than grace with no matching dispatch link.

    No ``bus_lifecycle_state`` filter. Bounded batch per call. Idempotent.
    Returns count of orphans stamped this pass.
    """
    from datetime import UTC, datetime, timedelta

    cutoff = (datetime.now(UTC) - timedelta(seconds=grace_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    prefix = f"{CDP_POINTER_BODY_PREFIX}%"

    with connect() as conn:
        rows = conn.execute(
            "SELECT thread, turn_number, body, created_at, from_agent "
            "FROM turns "
            "WHERE body LIKE ? AND created_at < ? "
            "AND id > (SELECT COALESCE(MAX(id), 0) - ? FROM turns) "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM thread_dispatch_links l "
            "  WHERE l.thread_id = turns.thread "
            "  AND turns.body LIKE '%execution_id=' || l.execution_id || ')%'"
            ") "
            "ORDER BY created_at ASC "
            "LIMIT ?",
            (prefix, cutoff, CDP_POINTER_SWEEP_ID_WINDOW, batch_limit),
        ).fetchall()

    stamped = 0
    for row in rows:
        row_dict: dict[str, Any] = dict(row)
        execution_id = parse_cdp_pointer_execution_id(row_dict.get("body"))
        if execution_id is None:
            continue
        thread_id = str(row_dict["thread"])
        if _sweep_one_orphan(
            thread_id=thread_id,
            execution_id=execution_id,
            pointer_from_agent=str(row_dict.get("from_agent") or "dispatch"),
        ):
            stamped += 1
    return stamped
