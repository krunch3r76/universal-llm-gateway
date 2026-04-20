"""
Helpers for eviction-wait terminal-state event emission.

Extracted from `wait_logic.py` to keep that module under the SLOC cap while
supporting the two distinct `routing.eviction.wait.timeout` exit paths —
`budget_exhausted` (waited full budget) and `non_transient` (first-iteration
bail when the retry trace no longer carries eviction_blocked_by_busy_models).
"""

from __future__ import annotations

from typing import Any


def build_exit_constraint_summary(trace: Any) -> list[dict]:
    """Per-candidate constraint snapshot for wait.timeout exit payload.

    Returns a list of ``{"gateway_id": str, "constraints_failed": list[str]}``
    — sorted constraint names per candidate — suitable for JSON payload. An
    empty list is returned when no trace or no candidates are available so the
    payload field is always present.
    """
    if not trace or not getattr(trace, "candidates", None):
        return []
    return [
        {
            "gateway_id": c.gateway.name,
            "constraints_failed": sorted({f.constraint for f in c.constraints_failed}),
        }
        for c in trace.candidates
    ]
