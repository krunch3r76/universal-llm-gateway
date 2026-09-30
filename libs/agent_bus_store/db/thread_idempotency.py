"""Idempotency-key lookup for ``create_thread`` retries (a:36915).

A client-side timeout on ``POST /threads`` can hide a committed insert; the
retry then mints a sibling (specimen: thread 13512, 2026-09-29). When the
caller sends ``idempotency_key``, these helpers resolve the key to the thread
the first call created so ``create_thread`` returns it instead.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .threads import get_thread_with_links


def thread_id_for_idempotency_key(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM threads WHERE idempotency_key = ? LIMIT 1", (key,)
    ).fetchone()
    return None if row is None else str(row["id"])


def replayed_thread_detail(thread_id: str) -> dict[str, Any]:
    """Existing thread detail, flagged so the route can answer 200 not 201."""
    detail = get_thread_with_links(thread_id)
    if detail is None:
        raise RuntimeError(
            f"idempotency_key resolved to thread {thread_id} but it is not readable"
        )
    detail["idempotent_replay"] = True
    return detail
