"""Resolve-or-create the cortex anchor entity for an OpenAI chat thread.

The anchor is a ``thread`` entity with id ``thread:openai-chat:{chat_id}``.
It accumulates ``user_turn(N)`` / ``assistant_turn(N)`` predicate-form
assertions written by the Phase 4 archive handlers, which this module
walks to compute the next free turn index.
"""

from __future__ import annotations

from .events import cx_async
from .turn_assertions import is_turn_assertion, next_turn_index


async def resolve_or_create_anchor(
    thread_kind: str,
    thread_key: str,
) -> tuple[str, int]:
    """Return ``(entity_id, current_turn_index)``.

    Looks up the thread anchor by id; creates it if cortex-api returns
    404. The returned index is the next free slot — 0 for a fresh
    anchor; one past the largest existing ``user_turn(N)`` /
    ``assistant_turn(N)`` predicate when extending an existing thread.

    Anchor ids follow ``thread:{thread_kind}:{thread_key}`` — e.g.
    ``thread:openai-chat:{chat_id}`` or ``thread:dispatch:{dispatch_thread_id}``.
    """
    anchor_id = f"thread:{thread_kind}:{thread_key}"

    get_res = await cx_async("entity_get", {"entity_id": anchor_id})
    if get_res.get("status_code") == 404:
        create_res = await cx_async(
            "entity_create",
            {
                "id": anchor_id,
                "type": "thread",
                "name": f"Thread {thread_kind} {thread_key}",
                "status": "confirmed",
                "workflow_state": "open",
                "notes": (
                    f"Created via thread persistence compactor "
                    f"({thread_kind}={thread_key})"
                ),
            },
        )
        if "error" in create_res:
            raise RuntimeError(
                f"thread persistence: failed to create anchor entity "
                f"{anchor_id}: {create_res['error']}"
            )
        return anchor_id, 0

    if "error" in get_res:
        raise RuntimeError(
            f"thread persistence: failed to load anchor {anchor_id}: {get_res['error']}"
        )

    turn_assertions = [
        a for a in (get_res.get("assertions") or []) if is_turn_assertion(a)
    ]
    return anchor_id, next_turn_index(turn_assertions)
