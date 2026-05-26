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

from universal_logging import get_logger

from .events import cx_async

logger = get_logger(__name__)


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
    assert_res = await cx_async(
        "assertions",
        {"entity_id": anchor_id, "superseded": False, "limit": 100},
    )
    items = assert_res.get("items", []) if isinstance(assert_res, dict) else []

    turns: list[tuple[int, str, str]] = []
    for ass in items:
        pred = ass.get("predicate_form")
        claim = ass.get("claim") or ""
        if not pred:
            continue

        if pred.startswith("user_turn("):
            role = "user"
        elif pred.startswith("assistant_turn("):
            role = "assistant"
        else:
            continue

        try:
            turn_idx = int(pred.split("(", 1)[1].rstrip(")"))
        except (ValueError, IndexError):
            continue

        # Reconstruct content from claim "User: <content>" /
        # "Assistant: <content>". len("User: ")=6, len("Assistant: ")=11.
        # len(role) + 2 matches in both cases — role is lowercase but
        # the title-cased claim prefix has the same length per role.
        prefix_len = len(role) + 2
        content = claim[prefix_len:] if len(claim) > prefix_len else claim
        turns.append((turn_idx, role, content))

    turns.sort(key=lambda x: (x[0], 0 if x[1] == "user" else 1))

    prefix = [{"role": r, "content": c} for _, r, c in turns]
    return prefix[-k:] if len(prefix) > k else prefix
