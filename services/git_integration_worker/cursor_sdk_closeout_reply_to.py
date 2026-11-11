"""Resolve the commissioner address for cursor-sdk closeout bus replies."""

from __future__ import annotations

import sqlite3

from agent_seat.registry import normalize_bus_address

from services.git_integration_worker.cursor_bus import CursorBusClient


def _admit_author_from_thread_db(thread_id: str) -> str | None:
    path = __import__("os").environ.get("AGENT_BUS_DB_PATH")
    if not path:
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                """
                SELECT from_agent FROM turns
                WHERE thread = ?
                  AND (
                    subject LIKE 'cursor-sdk generate admitted%'
                    OR subject LIKE 'cursor-sdk generate ADMITTED%'
                    OR subject LIKE 'cursor-sdk generate —%'
                  )
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(thread_id),),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    author = str(row[0] or "").strip()
    return author or None


def _agent_has_live_subscription(agent: str, thread_id: str) -> bool:
    from agent_bus_store.db.endpoint_subscriptions import current_subscription

    canon = normalize_bus_address(agent)
    for scope in (str(thread_id), "*"):
        row = current_subscription(canon, scope)
        if row and str(row.get("state") or "") == "subscribed":
            return True
    return False


def resolve_sdk_closeout_reply_to(
    *,
    caller_agent: str | None,
    thread_id: str,
    bus: CursorBusClient | None = None,
) -> str:
    """Prefer a commissioner with a live subscription over echoing ``cursor-sdk``.

    Order: non-``cursor-sdk`` ``caller_agent`` → admit author when subscribed on
    thread or wildcard → ``dispatch`` (dispatcher reroute may still wake a house).
    """
    _ = bus
    cand = str(caller_agent or "").strip()
    if cand and normalize_bus_address(cand) != "cursor-sdk":
        return cand
    admitted = _admit_author_from_thread_db(str(thread_id))
    if (
        admitted
        and normalize_bus_address(admitted) != "cursor-sdk"
        and _agent_has_live_subscription(admitted, str(thread_id))
    ):
        return admitted
    return "dispatch"
