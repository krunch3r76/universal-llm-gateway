"""Friction assertion ops — write (log) and read (list with charter filters)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..confidence_policy import CONFIDENCE_WEIGHT
from ..db import cortex_conn
from ..entity_aliases import resolve_entity_reference
from ..routes.assertions import _create_assertion_impl, _list_assertions_impl
from ._friction_charter_attrs import (
    _build_friction_provenance_attrs,
    _friction_charter_filters,
    _project_friction_summary_items,
)
from ._shared import (
    _FRICTION_CATEGORIES,
    _FRICTION_OWNER_TYPES,
    _VALID_CONFIDENCE,
    owner_entity_id,
    owner_type_of,
    record,
)

# Historical friction default when confidence is omitted or hypothesized.
# Distinct from CONFIDENCE_WEIGHT["hypothesized"] (0.20) — keep 0.5 so
# existing callers do not silently change score mass.
_FRICTION_DEFAULT_CONFIDENCE = "hypothesized"
_FRICTION_DEFAULT_SCORE = 0.5


def _resolve_friction_confidence(
    confidence: str | None,
    confidence_score: float | None,
) -> tuple[str, float] | dict[str, str]:
    """Honour caller confidence; reject invalid; default hypothesized/0.5."""
    resolved = _FRICTION_DEFAULT_CONFIDENCE if confidence is None else confidence
    if resolved not in _VALID_CONFIDENCE:
        return {
            "error": (
                f"Invalid confidence {confidence!r}. "
                f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
            )
        }
    if confidence_score is not None:
        return resolved, float(confidence_score)
    if resolved == _FRICTION_DEFAULT_CONFIDENCE:
        return resolved, _FRICTION_DEFAULT_SCORE
    return resolved, float(CONFIDENCE_WEIGHT[resolved])

logger = get_logger("cortex-api.dispatch_ops.assertions")


def _op_friction(
    owner: str | None = None,
    service: str | None = None,
    category: str | None = None,
    note: str | None = None,
    suggestion: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
    scoreboard_uri: str | None = None,
    actionable: bool | None = None,
    actionable_false_reason: str | None = None,
    checkpoint_turn: int | None = None,
    evidence_uris: list[str] | str | None = None,
    defer_enqueue: bool | None = None,
    confidence: str | None = None,
    confidence_score: float | None = None,
    **_: object,
) -> dict[str, Any]:
    """Log a friction assertion; protocol category requires charter context.

    ``confidence`` is honoured when supplied (ladder:
    confirmed/believed/suspected/hypothesized). Omitted → hypothesized/0.5.
    Invalid values are rejected — never silently downgraded.
    """
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
    conf_resolved = _resolve_friction_confidence(confidence, confidence_score)
    if isinstance(conf_resolved, dict):
        return conf_resolved
    resolved_confidence, resolved_score = conf_resolved
    if category == "protocol" and actionable is not False:
        root_present = charter_root is not None and str(charter_root).strip()
        if not (root_present and window_index is not None):
            return {
                "error": (
                    "protocol friction requires charter_root and window_index "
                    "(see file_charter_protocol_friction)"
                )
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
    provenance_attrs, prov_err = _build_friction_provenance_attrs(
        charter_root=charter_root,
        window_index=window_index,
        scoreboard_uri=scoreboard_uri,
        session_id=session_id,
        actionable=actionable,
        actionable_false_reason=actionable_false_reason,
        checkpoint_turn=checkpoint_turn,
        defer_enqueue=defer_enqueue,
    )
    if prov_err:
        return {"error": prov_err}

    claim = f"[{category or 'unclassified'}] {note}"
    if suggestion:
        claim += f" — Suggestion: {suggestion}"
    evidence = f"Friction observed by {agent or 'unknown'} during session"
    if session_id:
        evidence = f"[{session_id}] {evidence}"
    body: dict[str, Any] = {
        "entity_id": entity_id,
        "claim": claim,
        "confidence": resolved_confidence,
        "evidence": evidence,
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": resolved_score,
    }
    if agent:
        body["seeded_by"] = agent
    if provenance_attrs:
        body["attributes"] = provenance_attrs
    if evidence_uris:
        if isinstance(evidence_uris, str):
            evidence_uris = [evidence_uris]
        body["evidence_uris"] = [str(u) for u in evidence_uris]
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


def _op_frictions(
    owner: str | None = None,
    owner_type: str | None = None,
    service: str | None = None,
    category: str | None = None,
    seeded_by: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
    actionable: bool | None = None,
    since: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    intent: str | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    """List open friction assertions across friction-owning entities
    (service:/agent_skill:/ai_agent:), bracketed [category] claims."""
    resolved_intent = intent if intent in ("summary", "full") else "summary"
    if owner_type is not None and owner_type not in _FRICTION_OWNER_TYPES:
        return {
            "error": f"Invalid owner_type {owner_type!r}. Must be one of: {list(_FRICTION_OWNER_TYPES)}"
        }
    owner_arg = owner if owner is not None else service
    entity_id = owner_entity_id(owner_arg) if owner_arg else None
    entity_type = owner_type if (owner_type and entity_id is None) else None
    entity_type_in = (
        list(_FRICTION_OWNER_TYPES) if (entity_id is None and owner_type is None) else None
    )
    claim_filter = f"[{category}]" if category else None
    fetch_intent = "full" if any(
        v is not None for v in (charter_root, window_index, actionable, since)
    ) else resolved_intent
    fetch_limit = limit or (50 if fetch_intent == "full" else 7)
    result = _list_assertions_impl(
        entity_id=entity_id,
        entity_id_prefix=None,
        entity_type=entity_type,
        entity_type_in=entity_type_in,
        claim_filter=claim_filter,
        seeded_by=seeded_by,
        superseded=False if superseded is None else superseded,
        limit=fetch_limit,
        intent=fetch_intent,
        include_compaction_pointers=include_compaction_pointers,
    )
    if result.get("error"):
        return result

    if any(v is not None for v in (charter_root, window_index, actionable, since)):
        raw_items: list[dict[str, Any]] = []
        for item in result.get("items") or []:
            if isinstance(item, dict):
                raw_items.append(item)
            elif hasattr(item, "model_dump"):
                raw_items.append(item.model_dump(mode="json"))
        filtered = _friction_charter_filters(
            raw_items,
            charter_root=charter_root,
            window_index=window_index,
            actionable=actionable,
            since=since,
        )
        if resolved_intent == "summary":
            result["items"] = _project_friction_summary_items(filtered[: (limit or 7)])
        else:
            result["items"] = filtered[: (limit or 50)]
        result["intent"] = resolved_intent
    elif resolved_intent == "summary":
        pass
    if not result.get("error"):
        fix_cycle = (
            "Actionable row → codified bug ticket, investigate→execute fix cycle: investigate "
            "(cursor: role=cursor-consult; web: role=web-consult) → dense spec; "
            "execute (cursor: role=cursor-implement against spec; web: inline). "
            "DEFAULT investigate unless mechanical-only or a dense spec exists. "
            "lifecycle investigate→fix→report. friction() is log-only. "
            "Close via friction_close (agent_skill:|workflow:|todo:|superseded|wontfix). "
            "Skill: .cursor/skills/friction-review/SKILL.md or consult-routing § Codified bug reports."
        )
        if resolved_intent == "summary":
            result["_next"] = (
                "Deepen one row: cortex(tool=assertion_get, assertion_id=<id>). "
                "Full rows: re-call with intent=full. " + fix_cycle
            )
        else:
            result["_next"] = fix_cycle
    return result


__all__ = ["_op_friction", "_op_frictions"]
