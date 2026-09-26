"""Short thread mint — one transaction, no file I/O inside the write lock."""

from __future__ import annotations

import time
from typing import Any

from agent_bus_store.thread_classification import gate_thread_tags

from .connection import connect, now
from .lifecycle import _transition_lifecycle_state
from .threads import _next_auto_id, get_thread_with_links, set_thread_tags
from .turns import SlugExists


def mint_thread(
    *,
    slug: str,
    summary: str | None = None,
    tags: list[str] | None = None,
    lifecycle_state: str | None = None,
    enroll_charter_runner: bool = False,
    strict_slug: bool = False,
) -> tuple[dict[str, Any], float]:
    """Mint a thread row in one transaction; projection runs only after commit.

    Returns ``(thread_detail, mint_hold_ms)`` where ``mint_hold_ms`` is wall
    time in milliseconds for the ``connect()`` block including commit.
    """
    gated_tags = gate_thread_tags(
        tags, prior_tags=[], enroll_charter_runner=enroll_charter_runner
    )
    t0 = time.monotonic()
    with connect() as conn:
        if strict_slug:
            existing = conn.execute(
                "SELECT id FROM threads WHERE slug = ? LIMIT 1",
                (slug,),
            ).fetchone()
            if existing is not None:
                raise SlugExists(slug, existing["id"])

        thread_id = _next_auto_id(conn)
        ts = now()
        conn.execute(
            "INSERT INTO threads (id, slug, summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread_id, slug, summary, ts, ts),
        )
        if gated_tags:
            set_thread_tags(conn, thread_id, gated_tags)
        if lifecycle_state is not None:
            _transition_lifecycle_state(conn, thread_id, lifecycle_state, "create")

    mint_hold_ms = (time.monotonic() - t0) * 1000.0
    detail = get_thread_with_links(thread_id)
    if detail is None:
        raise RuntimeError(f"Failed to fetch newly minted thread {thread_id}")
    from ..cortex_thread_entity import ensure_thread_entity

    ensure_thread_entity(thread_id, slug, list(detail.get("tags") or gated_tags))
    return detail, mint_hold_ms
