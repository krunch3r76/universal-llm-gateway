"""Assertion update + supersede + single-get ops.

Split from ops_assertions.py to keep individual modules under SLOC budget per
[quality]. The umbrella ops_assertions module re-exports these names so the
dispatcher import surface stays backward-compatible.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ..routes.assertions import (
    _supersede_assertion_impl,
    _update_assertion_impl,
)
from ._assertions_shared import _UNSET, _emit_predicate_form_normalize_events
from ._shared import record

logger = get_logger("cortex-api.dispatch_ops.assertions")


def _op_assertion_get(assertion_id: int | None = None, **_: object) -> dict[str, Any]:
    """Read a single assertion by id.

    Used by `pipelines/predicate_extract/` for the §6.7 idempotency check
    (predicate_form IS NULL sentinel) without forcing a list-and-filter
    round trip. Returns the same shape as `_create_assertion_impl`'s
    `item` field — `predicate_form` included.
    """
    if assertion_id is None:
        return {"error": "assertion_id is required"}
    from ..db import cortex_conn, decode_row, query
    from ..models import AssertionItem
    from ..routes.assertions import _ASSERTION_COLS, _JSON_FIELDS

    with cortex_conn() as conn:
        rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
            (assertion_id,),
        )
    if not rows:
        return {"error": f"Assertion not found: {assertion_id}"}
    return AssertionItem(**decode_row(rows[0], _JSON_FIELDS)).model_dump(mode="json")


def _op_assertion_update(
    assertion_id: int | None = None,
    superseded_by: int | None = None,
    valid_until: str | None = None,
    confidence: str | None = None,
    confidence_score: float | None = None,
    review_status: str | None = None,
    reviewer: str | None = None,
    reviewed_at: str | None = None,
    review_notes: str | None = None,
    predicate_form: Any = _UNSET,
    force: bool = False,
    **_: object,
) -> dict[str, Any]:
    if assertion_id is None:
        return {"error": "assertion_id is required"}
    body: dict[str, Any] = {
        key: val
        for key, val in [
            ("superseded_by", superseded_by),
            ("valid_until", valid_until),
            ("confidence", confidence),
            ("confidence_score", confidence_score),
            ("review_status", review_status),
            ("reviewer", reviewer),
            ("reviewed_at", reviewed_at),
            ("review_notes", review_notes),
        ]
        if val is not None
    }
    # predicate_form: sentinel-default lets clear-to-null pass through.
    # When the agent sends {"predicate_form": null}, json-decode gives
    # predicate_form=None, distinct from "key omitted" (predicate_form=_UNSET).
    if predicate_form is not _UNSET:
        body["predicate_form"] = predicate_form  # may be None to clear
    if not body:
        return {"error": "No fields to update"}
    if force:
        body["force"] = True
    result = _update_assertion_impl(assertion_id, body)
    if "error" not in result:
        logger.info("cortex assertion_update: %d", assertion_id)
        record_kwargs: dict[str, Any] = {"assertion_id": assertion_id}
        if predicate_form is not _UNSET:
            record_kwargs["predicate_form_new"] = predicate_form
        record("mcp.cortex.assertion.updated", **record_kwargs)
        _emit_predicate_form_normalize_events(
            assertion_id=assertion_id,
            normalize_payload=result.get("predicate_form_normalize"),
        )
    return result


def _op_supersede(
    old_assertion_id: int | None = None,
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
    evidence_uris: list[str] | None = None,
    valid_from: str | None = None,
    derivation_type: str | None = None,
    reasoning_summary: str | None = None,
    seeded_by: str | None = None,
    chunk_id: str | None = None,
    confidence_score: float | None = None,
    session_id: str | None = None,
    agent: str | None = None,
    acknowledge_audit_gaps: list[str] | None = None,
    force: bool = False,
    **_: object,
) -> dict[str, Any]:
    for field, val in [
        ("old_assertion_id", old_assertion_id),
        ("entity_id", entity_id),
        ("claim", claim),
        ("confidence", confidence),
        ("evidence", evidence),
        ("session_id", session_id),
        ("agent", agent),
    ]:
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "old_assertion_id": old_assertion_id,
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence,
        "session_id": session_id,
        "agent": agent,
    }
    # Only include optional fields when explicitly provided — absent fields are
    # inherited from the superseded assertion at the route layer (model_fields_set
    # carryover).  This keeps the "simple rephrase" case ergonomic while allowing
    # callers to override or intentionally null-drop structured provenance fields.
    for key, val in [
        ("evidence_uris", evidence_uris),
        ("valid_from", valid_from),
        ("derivation_type", derivation_type),
        ("reasoning_summary", reasoning_summary),
        ("seeded_by", seeded_by),
        ("chunk_id", chunk_id),
        ("confidence_score", confidence_score),
    ]:
        if val is not None:
            body[key] = val
    if acknowledge_audit_gaps is not None:
        body["acknowledge_audit_gaps"] = acknowledge_audit_gaps
    if force:
        body["force"] = True
    result = _supersede_assertion_impl(body)
    if "error" not in result:
        new_id = result.get("new", {}).get("id")
        logger.info("cortex supersede: %d -> %s", old_assertion_id, new_id)
        record(
            "mcp.cortex.assertion.superseded",
            old_id=old_assertion_id,
            new_id=new_id,
        )
        if result.get("validation_warnings"):
            result["_next"] = (
                "auditor-validatability warnings present on superseded assertion — "
                "review and fix or pass acknowledge_audit_gaps=[...] to suppress "
                "(see agent_skill:auditor-validatable-confidence)"
            )
    return result


__all__ = ["_op_assertion_get", "_op_assertion_update", "_op_supersede"]
