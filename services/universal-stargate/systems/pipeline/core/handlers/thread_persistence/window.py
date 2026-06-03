"""Build the text-only referential window from anchor assertions.

Reads all assertions on the thread anchor entity in a single cortex
round-trip, projects non-superseded turn assertions
(predicate-form ``user_turn(N)`` / ``assistant_turn(N)``) into a
``[{role, content}]`` prefix consumed by the ``frontier_dispatch_request``
Mode-1 ``handler_inputs.messages`` binding, and prepends a §6.10
consolidation summary message when one is present on the anchor.

The latest user turn is appended by the caller (``resolve_messages``);
the prefix excludes it. ``role=system`` is never included — system content
flows through ``FrontierRequest.system`` separately.

Summary prepend (Stage A): when a non-superseded ``thread_summary(N)``
assertion is present, a ``system``-role message is inserted at the head of
the returned list. This gives the model compressed context from before the
hot-tail window without requiring the collapsed turns to be superseded.
"""

from __future__ import annotations

from .thread_compression import parse_thread_compression_boundaries
from .turn_assertions import (
    extract_latest_summary,
    is_turn_assertion,
    load_all_assertions,
    turns_from_assertions,
)

_SUMMARY_CLAIM_PREFIX = "archive summary: "
_SUMMARY_MSG_HEADER = "[Archive summary]\n"


async def build_referential_window(
    anchor_id: str,
    *,
    k: int,
) -> list[dict[str, str]]:
    """Return the assembled message prefix for the referential window.

    Performs a single ``entity_get`` to load all assertions, then:

    1. Extracts the latest non-superseded §6.10 consolidation summary.
    2. Projects non-superseded turn assertions into ``(turn_index, role,
       content)`` tuples, sorted ascending, user before assistant.
    3. Takes the last ``k`` turns as the hot-tail window.
    4. Prepends the summary as a ``system``-role message when present.

    Returns an empty list when the anchor has no turn assertions yet.
    When ``k`` is larger than the number of turns, all turns are returned.
    """
    all_items = await load_all_assertions(anchor_id)
    summary = extract_latest_summary(all_items)
    turn_items = [a for a in all_items if is_turn_assertion(a)]
    turns = turns_from_assertions(turn_items)
    hot_tail_start: int | None = None
    if summary is not None:
        _, hot_tail_start = parse_thread_compression_boundaries(summary)
    if hot_tail_start is not None:
        hot_turns = [(idx, r, c) for idx, r, c in turns if idx >= hot_tail_start]
    else:
        hot_turns = turns
    prefix = [{"role": r, "content": c} for _, r, c in hot_turns]
    window = prefix[-k:] if len(prefix) > k else prefix

    if not summary:
        return window

    claim = summary.get("claim") or ""
    summary_text = (
        claim[len(_SUMMARY_CLAIM_PREFIX) :]
        if claim.startswith(_SUMMARY_CLAIM_PREFIX)
        else claim
    )
    summary_msg: dict[str, str] = {
        "role": "system",
        "content": f"{_SUMMARY_MSG_HEADER}{summary_text}",
    }
    return [summary_msg] + window
