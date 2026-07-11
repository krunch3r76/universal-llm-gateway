"""Assertion write ops — assert, observe, friction, friction_close.

Split from ops_assertions.py to keep individual modules under SLOC budget per
[quality]. The umbrella ops_assertions module re-exports these names so the
dispatcher import surface stays backward-compatible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..db import cortex_conn
from ..entity_aliases import resolve_entity_reference
from ..routes.assertions import _create_assertion_impl
from ..write_discipline_nudge import attach_write_discipline, build_assert_nudge
from ._assertions_shared import (
    _emit_predicate_form_normalize_events,
    _project_seeded_by,
)
from ._friction_close_impl import close_friction_assertion, validate_resolution_kind
from ._shared import (
    _DEFAULT_USER_ENTITY,
    _FRICTION_CATEGORIES,
    _VALID_CONFIDENCE,
    owner_entity_id,
    owner_type_of,
    record,
)

logger = get_logger("cortex-api.dispatch_ops.assertions")


def _op_friction_close(
    assertion_id: int | None = None,
    resolution_kind: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    evidence: str | None = None,
    resolution_note: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Close an open friction by superseding it with a confirmed resolution row."""
    if not assertion_id:
        return {"error": "assertion_id required for friction_close"}
    if not resolution_kind:
        return {"error": "resolution_kind required for friction_close"}
    kind_err = validate_resolution_kind(resolution_kind)
    if kind_err:
        return {"error": kind_err}

    return close_friction_assertion(
        assertion_id,
        resolution_kind,
        agent=agent or "unknown",
        session_id=session_id or "friction-close",
        evidence=evidence,
        resolution_note=resolution_note,
    )


def _op_assert(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
    evidence_uris: list[str] | str | None = None,
    seeded_by: str | None = None,
    derivation_type: str | None = None,
    confidence_score: float | None = None,
    observed_at: str | None = None,
    valid_from: str | None = None,
    chunk_id: str | None = None,
    reasoning_summary: str | None = None,
    prospective_summary: str | None = None,
    events_json: str | None = None,
    artifact_uri: str | None = None,
    artifact_storage: str | None = None,
    predicate_form: str | None = None,
    force: bool = False,
    supersedes_id: int | None = None,
    acknowledge_audit_gaps: list[str] | None = None,
    dry_run: bool = False,
    attributes: dict[str, Any] | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    required_fields = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence,
    }
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    assert entity_id is not None
    write_nudge = None
    with cortex_conn() as conn:
        try:
            resolved = resolve_entity_reference(
                conn,
                entity_id,
                resolve_aliases=resolve_aliases,
                raw_id=raw_id,
                label="entity",
            )
        except HTTPException as exc:
            return {"error": exc.detail, "status_code": exc.status_code}
        canonical_entity_id = resolved.entity_id if not raw_id else entity_id
        try:
            write_nudge = build_assert_nudge(
                conn,
                canonical_entity_id,
                claim,
                confidence or "believed",
                predicate_form=predicate_form,
            )
        except Exception:  # noqa: BLE001 — advisory nudge must never block the write
            logger.warning(
                "build_assert_nudge failed for %s — proceeding without advisory",
                canonical_entity_id,
                exc_info=True,
            )
            write_nudge = None
    entity_id = canonical_entity_id
    assert confidence is not None
    if confidence not in _VALID_CONFIDENCE:
        return {
            "error": f"Invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
        }
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence,
    }
    if evidence_uris:
        if isinstance(evidence_uris, str):
            evidence_uris = [evidence_uris]
        body["evidence_uris"] = [str(u) for u in evidence_uris]
    if observed_at is None:
        observed_at = datetime.now(UTC).isoformat()
    for key, val in [
        ("seeded_by", seeded_by),
        ("derivation_type", derivation_type),
        ("confidence_score", confidence_score),
        ("observed_at", observed_at),
        ("valid_from", valid_from),
        ("chunk_id", chunk_id),
        ("reasoning_summary", reasoning_summary),
        ("prospective_summary", prospective_summary),
        ("events_json", events_json),
        ("artifact_uri", artifact_uri),
        ("artifact_storage", artifact_storage),
        ("predicate_form", predicate_form),
    ]:
        if val is not None:
            body[key] = val
    if force:
        body["force"] = True
    if supersedes_id is not None:
        body["supersedes_id"] = supersedes_id
    if acknowledge_audit_gaps is not None:
        body["acknowledge_audit_gaps"] = acknowledge_audit_gaps
    if dry_run:
        body["dry_run"] = True
    if attributes is not None:
        body["attributes"] = attributes
    if derivation_type is None or confidence_score is None:
        logger.warning(
            "cortex assert: missing derivation_type=%s or confidence_score=%s — "
            "these will become mandatory in a future version",
            derivation_type,
            confidence_score,
        )
    projection_tag = None
    if seeded_by is not None:
        body["seeded_by"], projection_tag = _project_seeded_by(seeded_by)
    result = _create_assertion_impl(body)
    if "error" not in result:
        if seeded_by is not None:
            result["seeded_by_input"] = seeded_by
            result["seeded_by"] = body["seeded_by"]
            result["seeded_by_projection"] = projection_tag
        if result.get("dry_run"):
            if write_nudge is not None:
                attach_write_discipline(result, write_nudge)
            return result
        logger.info("cortex assert: %s — %s (%s)", entity_id, claim[:60], confidence)
        record(
            "mcp.cortex.assertion.seeded", entity_id=entity_id, confidence=confidence
        )
        _emit_predicate_form_normalize_events(
            assertion_id=(result.get("item") or {}).get("id"),
            normalize_payload=result.get("predicate_form_normalize"),
        )
        if result.get("validation_warnings"):
            warnings = result["validation_warnings"]
            # category field on each warning is the canonical discriminator.
            # Legacy warnings (pre-category field) default to staging via the
            # ValidationDiagnostic dataclass default; raw dicts that omit
            # category are treated as staging here too for safety.
            has_staging = any(
                w.get("category", "staging") == "staging" for w in warnings
            )
            has_auditor = any(w.get("category") == "auditor" for w in warnings)
            hints = []
            if has_staging:
                hints.append(
                    "assertion routed to staging — to graduate to committed, "
                    "supersede with the missing reasoning_summary or chunk_id "
                    "(carryover preserves all other fields; the new row is the "
                    "committed version). reasoning_summary is immutable "
                    "post-creation per cortex-provenance-substrate-v1.3-additions "
                    "§7.5.3 — assertion_update does not accept it."
                )
            if has_auditor:
                hints.append(
                    "auditor-validatability warnings present — review and fix or pass "
                    "acknowledge_audit_gaps=[...] to suppress (see agent_skill:auditor-validatable-confidence)"
                )
            if hints:
                result["_next"] = "; ".join(hints)
        if write_nudge:
            attach_write_discipline(result, write_nudge)
    return result


def _op_observe(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str = "believed",
    agent: str | None = None,
    evidence: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        entity_id = _DEFAULT_USER_ENTITY
    if not entity_id:
        return {
            "error": "entity_id is required (set CORTEX_DEFAULT_USER_ENTITY env var for a default)"
        }
    if not claim:
        return {"error": "claim is required"}
    if confidence not in _VALID_CONFIDENCE:
        return {
            "error": f"Invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
        }
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence or "Agent observation during session",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": 0.8 if confidence == "believed" else 0.6,
    }
    if agent:
        body["seeded_by"] = agent
    result = _create_assertion_impl(body)
    if "error" not in result:
        logger.info(
            "cortex observe: %s — %s (%s, by %s)",
            entity_id,
            claim[:60],
            confidence,
            agent or "unknown",
        )
        record(
            "mcp.cortex.observation.seeded",
            entity_id=entity_id,
            confidence=confidence,
            agent=agent,
        )
    return result


def _op_friction(
    owner: str | None = None,
    service: str | None = None,
    category: str | None = None,
    note: str | None = None,
    suggestion: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if (
        owner is not None
        and service is not None
        and owner_entity_id(owner) != owner_entity_id(service)
    ):
        return {
            "error": "Supply either owner= or service= (back-compat alias), not both with different values."
        }
    owner_arg = owner if owner is not None else service
    if not owner_arg:
        return {
            "error": "owner is required (service:/agent_skill:/ai_agent: entity ID, or bare slug -> service:)"
        }
    if not note:
        return {"error": "note is required — describe what went wrong"}
    if category and category not in _FRICTION_CATEGORIES:
        return {
            "error": f"Invalid category {category!r}. Must be one of: {sorted(_FRICTION_CATEGORIES)}"
        }
    if ":" in owner_arg and owner_type_of(owner_arg) is None:
        return {
            "error": f"Unsupported owner namespace in {owner_arg!r}. Allowed prefixes: service:, agent_skill:, ai_agent: (or a bare slug -> service:)."
        }
    entity_id = owner_entity_id(owner_arg)
    if owner_type_of(entity_id) != "service":
        with cortex_conn() as conn:
            try:
                resolved = resolve_entity_reference(conn, entity_id, label="owner")
            except HTTPException as exc:
                return {
                    "error": f"owner {entity_id} not found ({exc.detail}); create the entity before logging friction against it.",
                    "status_code": exc.status_code,
                }
        entity_id = resolved.entity_id
    claim = f"[{category or 'unclassified'}] {note}"
    if suggestion:
        claim += f" — Suggestion: {suggestion}"
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": "hypothesized",
        "evidence": f"Friction observed by {agent or 'unknown'} during session",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": 0.5,
    }
    if agent:
        body["seeded_by"] = agent
    result = _create_assertion_impl(body)
    if "error" not in result:
        logger.info("cortex friction: %s/%s — %s", entity_id, category, note[:60])
        record(
            "mcp.cortex.friction.logged",
            owner=entity_id,
            owner_type=owner_type_of(entity_id),
            category=category or "unclassified",
            agent=agent,
        )
    return result


__all__ = ["_op_assert", "_op_friction", "_op_friction_close", "_op_observe"]
