"""Resolve-or-create the cortex anchor entity for an OpenAI chat thread.

The anchor is a ``thread`` entity with id ``thread:openai-chat:{chat_id}``.
It accumulates ``user_turn(N)`` / ``assistant_turn(N)`` predicate-form
assertions written by the Phase 4 archive handlers, which this module
walks to compute the next free turn index.
"""

from __future__ import annotations

from universal_logging import get_logger

from .events import cx_async

logger = get_logger(__name__)


async def resolve_or_create_anchor(
    chat_id: str,
) -> tuple[str, int]:
    """Return ``(entity_id, current_turn_index)``.

    Looks up the thread anchor by id; creates it if cortex-api returns
    404. The returned index is the next free slot — 0 for a fresh
    anchor; one past the largest existing ``user_turn(N)`` /
    ``assistant_turn(N)`` predicate when extending an existing thread.
    """
    anchor_id = f"thread:openai-chat:{chat_id}"

    get_res = await cx_async("entity_get", {"entity_id": anchor_id})
    if get_res.get("status_code") == 404:
        create_res = await cx_async(
            "entity_create",
            {
                "id": anchor_id,
                "type": "thread",
                "name": f"OpenAI Chat Thread {chat_id}",
                "status": "confirmed",
                "workflow_state": "open",
                "notes": (
                    f"Created via cortex-chat-openai compactor for chat_id: {chat_id}"
                ),
            },
        )
        if "error" in create_res:
            logger.error(
                "Failed to create anchor entity %s: %s",
                anchor_id,
                create_res["error"],
            )
        return anchor_id, 0

    assert_res = await cx_async(
        "assertions",
        {"entity_id": anchor_id, "superseded": False, "limit": 100},
    )
    items = assert_res.get("items", []) if isinstance(assert_res, dict) else []

    max_turn = -1
    for ass in items:
        pred = ass.get("predicate_form")
        if not pred:
            continue
        if not (pred.startswith("user_turn(") or pred.startswith("assistant_turn(")):
            continue
        try:
            val = int(pred.split("(", 1)[1].rstrip(")"))
        except (ValueError, IndexError):
            continue
        if val > max_turn:
            max_turn = val

    return anchor_id, max_turn + 1
