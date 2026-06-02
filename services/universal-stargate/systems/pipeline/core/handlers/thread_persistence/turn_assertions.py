"""Turn-assertion load and parse helpers for thread persistence.

Uses ``entity_get`` (unlimited assertion fetch) rather than the paginated
``assertions`` list op so turn-index allocation stays exact regardless of
thread length. Phase C summarization (``PipelineCompactionSummarized``) will
supersede older turns and keep the active set bounded long-term; until that
wires in, correctness depends on reading the full non-superseded stream.
"""

from __future__ import annotations

from typing import Any

from .events import cx_async

_USER_TURN_PREFIX = "user_turn("
_ASSISTANT_TURN_PREFIX = "assistant_turn("


def is_turn_assertion(assertion: dict[str, Any]) -> bool:
    """True when *assertion* is a non-superseded user/assistant turn row."""
    if assertion.get("superseded_by"):
        return False
    pred = assertion.get("predicate_form")
    if not pred:
        return False
    return pred.startswith(_USER_TURN_PREFIX) or pred.startswith(_ASSISTANT_TURN_PREFIX)


def parse_turn_index(predicate_form: str) -> int | None:
    """Extract N from ``user_turn(N)`` / ``assistant_turn(N)``."""
    if not (
        predicate_form.startswith(_USER_TURN_PREFIX)
        or predicate_form.startswith(_ASSISTANT_TURN_PREFIX)
    ):
        return None
    try:
        return int(predicate_form.split("(", 1)[1].rstrip(")"))
    except (ValueError, IndexError):
        return None


def next_turn_index(assertions: list[dict[str, Any]]) -> int:
    """Return the next free turn slot (0 when no turn assertions exist)."""
    max_turn = -1
    for ass in assertions:
        if not is_turn_assertion(ass):
            continue
        idx = parse_turn_index(ass["predicate_form"])
        if idx is not None and idx > max_turn:
            max_turn = idx
    return max_turn + 1


def turns_from_assertions(
    assertions: list[dict[str, Any]],
) -> list[tuple[int, str, str]]:
    """Project turn assertions to ``(turn_index, role, content)`` tuples."""
    turns: list[tuple[int, str, str]] = []
    for ass in assertions:
        if not is_turn_assertion(ass):
            continue
        pred = ass["predicate_form"]
        claim = ass.get("claim") or ""
        if pred.startswith(_USER_TURN_PREFIX):
            role = "user"
        else:
            role = "assistant"
        idx = parse_turn_index(pred)
        if idx is None:
            continue
        prefix_len = len(role) + 2
        content = claim[prefix_len:] if len(claim) > prefix_len else claim
        turns.append((idx, role, content))
    turns.sort(key=lambda x: (x[0], 0 if x[1] == "user" else 1))
    return turns


async def load_turn_assertions(anchor_id: str) -> list[dict[str, Any]]:
    """Load all non-superseded turn assertions on a thread anchor."""
    res = await cx_async("entity_get", {"entity_id": anchor_id})
    if res.get("status_code") == 404:
        return []
    if "error" in res:
        raise RuntimeError(
            f"thread persistence: failed to load anchor {anchor_id}: {res['error']}"
        )
    raw = res.get("assertions") or []
    return [a for a in raw if is_turn_assertion(a)]
