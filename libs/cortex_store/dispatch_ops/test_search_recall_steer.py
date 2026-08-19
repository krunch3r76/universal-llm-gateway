"""Search _next steering toward recall for memory-shaped cortex.search queries.

Unit tests for steer_search_toward_recall — G2 Phase 1 teaching path on the
dispatch router, not the HTTP OpenAPI search surface.
"""

from __future__ import annotations

from cortex_store.dispatch_ops.workflow_hints import (
    _WORKFLOW_HINTS,
    steer_search_toward_recall,
)


def test_memory_shaped_query_overwrites_next_with_recall_steer() -> None:
    """Memory-shaped q overwrites result _next with a recall(op= steer line."""
    result: dict = {"results": []}
    parsed = {"query": "what do we know about chase escrow"}
    steer_search_toward_recall(result, parsed)
    assert "_next" in result
    assert "recall(op=" in result["_next"]


def test_non_memory_query_leaves_static_search_hint() -> None:
    """Non-memory q is a no-op; static search hint still names recall as memory door."""
    result: dict = {"results": []}
    parsed = {"query": "UDS transport invariant"}
    steer_search_toward_recall(result, parsed)
    assert "_next" not in result
    static = _WORKFLOW_HINTS["search"]
    assert "recall" in static.lower()
