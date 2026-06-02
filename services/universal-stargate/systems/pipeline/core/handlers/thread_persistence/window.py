"""Build the text-only referential window from anchor assertions.

Reads non-superseded turn assertions (predicate-form
``user_turn(N)`` / ``assistant_turn(N)``) on the thread anchor entity
and projects them into a ``[{role, content}]`` prefix consumed by the
``frontier_dispatch_request`` Mode-1 ``handler_inputs.messages``
binding. The latest user turn is appended by the caller
(``resolve_messages``); the prefix excludes it. ``role=system`` is
never included — system content flows through ``FrontierRequest.system``
separately.
"""

from __future__ import annotations

from .turn_assertions import load_turn_assertions, turns_from_assertions


async def build_referential_window(
    anchor_id: str,
    *,
    k: int,
) -> list[dict[str, str]]:
    """Return the last ``k`` turn messages as ``[{role, content}]``.

    Sorted by ``turn_index`` ascending, with user turns ordered before
    assistant turns at the same index for deterministic replay. Returns
    an empty list when the anchor has no turn assertions yet.
    """
    items = await load_turn_assertions(anchor_id)
    turns = turns_from_assertions(items)
    prefix = [{"role": r, "content": c} for _, r, c in turns]
    return prefix[-k:] if len(prefix) > k else prefix
