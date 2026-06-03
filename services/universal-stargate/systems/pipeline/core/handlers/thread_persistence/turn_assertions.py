"""Turn-assertion load and parse helpers for thread persistence.

Uses ``entity_get`` (unlimited assertion fetch) rather than the paginated
``assertions`` list op so turn-index allocation stays exact regardless of
thread length. Stage C summarization will supersede older turns and keep the
active set bounded long-term; in Stage A the full non-superseded stream is read.
"""

from __future__ import annotations

from typing import Any

from .events import cx_async

_USER_TURN_PREFIX = "user_turn("
_ASSISTANT_TURN_PREFIX = "assistant_turn("

# §6.10 consolidation summary — mirrors constants in compaction_summarize.py.
# Both sides detect the same claim/predicate shape; keep values in sync.
_SUMMARY_CLAIM_PREFIX = "archive summary: "
_SUMMARY_PRED_PREFIX = "thread_summary("


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


def _is_consolidation_summary(assertion: dict[str, Any]) -> bool:
    """True when *assertion* is a non-superseded §6.10 consolidation summary.

    Mirrors ``is_thread_summary_assertion`` in ``compaction_summarize.py``
    but adds a claim-prefix check to filter out malformed predicates.
    ∀ a: _is_consolidation_summary(a) ⟹ claim.startswith("archive summary: ")
    """
    if assertion.get("superseded_by"):
        return False
    pred = assertion.get("predicate_form") or ""
    if not pred.startswith(_SUMMARY_PRED_PREFIX):
        return False
    claim = assertion.get("claim") or ""
    return claim.startswith(_SUMMARY_CLAIM_PREFIX)


def _parse_summary_boundary(predicate_form: str) -> int | None:
    """Extract N from ``thread_summary(N)``."""
    if not predicate_form.startswith(_SUMMARY_PRED_PREFIX):
        return None
    try:
        return int(predicate_form.split("(", 1)[1].rstrip(")"))
    except (ValueError, IndexError):
        return None


def extract_latest_summary(
    assertions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the non-superseded consolidation summary with the highest turn boundary.

    Used by ``build_referential_window`` to prepend a compressed-history
    message before the hot-tail window. Returns None when no summary exists.

    ∀ result: result = argmax_{N} {a | _is_consolidation_summary(a)}
    """
    best: dict[str, Any] | None = None
    best_n = -1
    for ass in assertions:
        if not _is_consolidation_summary(ass):
            continue
        n = _parse_summary_boundary(ass.get("predicate_form") or "")
        if n is not None and n > best_n:
            best_n = n
            best = ass
    return best


async def load_all_assertions(anchor_id: str) -> list[dict[str, Any]]:
    """Load ALL assertions on a thread anchor (superseded and non-superseded).

    Returns raw list suitable for summary detection + turn filtering in one
    cortex round-trip. Callers filter by predicate/superseded as needed.
    """
    res = await cx_async("entity_get", {"entity_id": anchor_id})
    if res.get("status_code") == 404:
        return []
    if "error" in res:
        raise RuntimeError(
            f"thread persistence: failed to load anchor {anchor_id}: {res['error']}"
        )
    return res.get("assertions") or []


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
