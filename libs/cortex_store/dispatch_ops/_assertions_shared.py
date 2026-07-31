"""Shared helpers for the ops_assertions_* dispatch-op modules.

Lives separately from dispatch_ops/_shared.py (which is package-wide) so the
predicate-form normalize emitter and the _UNSET sentinel can be imported by
both ops_assertions_write and ops_assertions_update without either depending
on the umbrella ops_assertions module.
"""

from __future__ import annotations

from typing import Any

from agent_seat import known_families, seat_to_family

from ._shared import record


def _project_seeded_by(seeded_by: str) -> tuple[str, str]:
    """Map a seeded_by value to (stored_value, projection_tag).

    seat→family            → (family, "seat_to_family")
    already a bare family   → (seeded_by, "identity")
    non-projectable (pipeline ids, unrecognized) → (seeded_by, "passthrough_unrecognized")

    NEVER raises / NEVER returns an error — non-agent provenance (e.g.
    pipeline context.pipeline.id like ``summarize_thread_v1``) must survive to
    keep the chat-archive pipeline green (review C1). Operators grep
    ``passthrough_unrecognized`` to catch genuine seat-level drift.
    """
    if seeded_by in known_families():
        return seeded_by, "identity"
    projected = seat_to_family(seeded_by)
    if projected is not None:
        return projected, "seat_to_family"
    return seeded_by, "passthrough_unrecognized"


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


def _coerce_evidence_uris(value: list[str] | str | None) -> list[str] | None:
    """Normalise an evidence_uris argument to a list of strings.

    Codeblind seats routinely send a lone URI as a bare string; both the write
    and the update op accept that shape so a single citation does not require
    the caller to know it must be wrapped.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value.strip() else None
    return [str(item) for item in value]


_UNSET: Any = object()
"""Sentinel for nullable fields where None is a meaningful clearing value
distinct from "argument absent". See _op_assertion_update.predicate_form."""


__all__ = [
    "_UNSET",
    "_coerce_evidence_uris",
    "_emit_predicate_form_normalize_events",
    "_project_seeded_by",
]
