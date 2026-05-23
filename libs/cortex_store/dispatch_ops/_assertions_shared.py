"""Shared helpers for the ops_assertions_* dispatch-op modules.

Lives separately from dispatch_ops/_shared.py (which is package-wide) so the
predicate-form normalize emitter and the _UNSET sentinel can be imported by
both ops_assertions_write and ops_assertions_update without either depending
on the umbrella ops_assertions module.
"""

from __future__ import annotations

from typing import Any

from ._shared import record


def _emit_predicate_form_normalize_events(
    *, assertion_id: int | None, normalize_payload: dict[str, Any] | None
) -> None:
    """Emit mcp.cortex.predicate.* signals from a route's normalize envelope.

    Sibling-family parity (Q5.5 / dispatch packet): every cortex-api write that
    surfaces ``predicate_form_normalize`` on its response fires
    ``mcp.cortex.predicate.normalized`` here; ``requires_human_review`` adds a
    parallel ``mcp.cortex.predicate.review.required`` signal. Routes stay
    HTTP-only — emission lives at the dispatcher contract layer alongside the
    existing ``mcp.cortex.assertion.*`` family.
    """
    if not normalize_payload:
        return
    common: dict[str, Any] = {
        "assertion_id": assertion_id,
        "predicate_form_in": normalize_payload.get("predicate_form_in"),
        "canonical_form": normalize_payload.get("canonical_form"),
        "classes_applied": normalize_payload.get("classes_applied") or [],
        "normalized": bool(normalize_payload.get("normalized")),
    }
    record(
        "mcp.cortex.predicate.normalized",
        requires_human_review=bool(normalize_payload.get("requires_human_review")),
        **common,
    )
    if normalize_payload.get("requires_human_review"):
        record("mcp.cortex.predicate.review.required", **common)


_UNSET: Any = object()
"""Sentinel for nullable fields where None is a meaningful clearing value
distinct from "argument absent". See _op_assertion_update.predicate_form."""


__all__ = ["_UNSET", "_emit_predicate_form_normalize_events"]
