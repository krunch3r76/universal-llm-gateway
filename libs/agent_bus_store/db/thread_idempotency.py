"""Idempotency-key lookup for ``create_thread`` retries (a:36915).

A client-side timeout on ``POST /threads`` can hide a committed insert; the
retry then mints a sibling (specimen: thread 13512, 2026-09-29). When the
caller sends ``idempotency_key``, these helpers resolve the key to the thread
the first call created so ``create_thread`` returns it instead.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from .connection import write_connect
from .threads import get_thread_with_links

_IDEMPOTENT_TURN1_WAIT_S = 30.0


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


def _turn1_row(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM turns WHERE thread = ? AND turn_number = 1 LIMIT 1",
        (thread_id,),
    ).fetchone()
    return None if row is None else dict(row)


def existing_first_turn_for_idempotent_send(
    thread_id: str,
    *,
    idempotent_replay: bool,
) -> dict[str, Any] | None:
    """Return turn 1 under write serialization when a send must not insert again.

    Idempotent replays wait (bounded) for an in-flight first turn on the same
    thread so concurrent retries cannot both miss turn 1 outside the lock.
    """
    deadline = time.monotonic() + _IDEMPOTENT_TURN1_WAIT_S
    while True:
        with write_connect() as conn:
            existing = _turn1_row(conn, thread_id)
            if existing is not None:
                return existing
            if not idempotent_replay:
                return None
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"idempotent replay timed out waiting for turn 1 on thread {thread_id!r}"
            )
        time.sleep(0.002)
