"""Friction assertion ops — write (log) and read (list with anchor filters).

Protocol category accepts either charter ``{charter_root, window_index}`` or
continuity ``{root_thread, cp_ordinal}`` anchor variants when sweep-eligible.
"""

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
    _PROTOCOL_ANCHOR_REQUIRED_ERROR,
    PROTOCOL_ANCHOR_VARIANTS,
    _build_friction_provenance_attrs,
    _charter_variant_complete,
    _continuity_variant_complete,
    _friction_anchor_view,
    _validate_anchor_completeness,
)
from ._related_edges import prepare_related_edges, write_related_edges
from ._shared import (
    _FRICTION_CATEGORIES,
    _VALID_CONFIDENCE,
    owner_entity_id,
    owner_type_of,
    record,
)
from ._write_validation import (
    collect_missing_required,
    field_error,
    is_missing,
    resolve_mutually_exclusive_aliases,
    validation_error_response,
)

# Historical friction default when confidence is omitted or hypothesized.
# Distinct from CONFIDENCE_WEIGHT["hypothesized"] (0.20) — keep 0.5 so
# existing callers do not silently change score mass.
_FRICTION_DEFAULT_CONFIDENCE = "hypothesized"
_FRICTION_DEFAULT_SCORE = 0.5

# Bus/endpoint addresses callers paste as ``owner``. They are not service
# slugs. ``ai_agent:web-anthropic`` is the live seat entity; ``claude-web``
# and ``claude.ai`` are aliases of that endpoint. ``cursor`` / ``cursor-sdk``
# have no ai_agent row — the miss steers to a real service or skill owner
# instead of inviting a minted ``service:cursor``.
_SEAT_ENDPOINT_OWNER: dict[str, str] = {
    "web-anthropic": "ai_agent:web-anthropic",
    "claude-web": "ai_agent:web-anthropic",
    "claude.ai": "ai_agent:web-anthropic",
    "cursor": "ai_agent:cursor",
    "cursor-sdk": "ai_agent:cursor-sdk",
}
_FRICTION_OWNER_EXAMPLES = (
    "service:mcp-server, service:universal-stargate, "
    "service:agent-bus, service:cortex-api, or agent_skill:<slug>"
)


def _seat_endpoint_owner(entity_id: str) -> str | None:
    """Map a bare or prefixed seat address to its canonical ai_agent id."""
    slug = entity_id
    for prefix in ("service:", "ai_agent:", "agent_skill:"):
        if entity_id.startswith(prefix):
            slug = entity_id.removeprefix(prefix)
            break
    return _SEAT_ENDPOINT_OWNER.get(slug)


def _seat_owner_not_found(given: str, canonical: str) -> dict[str, Any]:
    """404 that names real friction owners and refuses a minted service slug."""
    return {
        "error": (
            f"owner {given!r} is a seat/endpoint address, not a service slug. "
            f"Do not mint {given!r}. "
            f"Seat observations belong on {canonical!r} when that entity exists; "
            "otherwise file against the owning service or skill "
            f"({_FRICTION_OWNER_EXAMPLES})."
        ),
        "status_code": 404,
    }


def _retry_seat_endpoint_owner(
    conn: Any,
    entity_id: str,
) -> str | dict[str, Any] | None:
    """Resolve a known seat alias, or None when *entity_id* is not one.

    A hit returns the live ``ai_agent:`` id. A miss returns the steer payload.
    Non-404 resolve errors pass through with their status code.
    """
    target = _seat_endpoint_owner(entity_id)
    if target is None:
        return None
    if target != entity_id:
        try:
            return resolve_entity_reference(conn, target, label="owner").entity_id
        except HTTPException as exc:
            if exc.status_code != 404:
                return {
                    "error": f"owner {entity_id}: {exc.detail}",
                    "status_code": exc.status_code,
                }
    return _seat_owner_not_found(entity_id, target)


def _resolve_friction_owner_entity_id(entity_id: str) -> str | dict[str, Any]:
    """Resolve friction owner to a live entity id, or an error payload.

    Service owners historically skipped ``resolve_entity_reference``, so
    ``service:agent_bus`` (tool/manage underscore) 404'd while the canonical
    entity is ``service:agent-bus`` (friction a:37201). Soft ``_``→``-`` retry
    only after an exact **404** miss so underscore-canonical services stay
    exact-first and ambiguous-alias 400s are not rewritten as not-found.
    Known seat addresses (``web-anthropic``, ``claude-web``, ``claude.ai``,
    ``cursor``, ``cursor-sdk``) retry their ``ai_agent:`` owner after a
    service-slug miss. When that row is absent the error names real
    ``service:`` / ``agent_skill:`` owners and does not say to mint the seat.
    """
    with cortex_conn() as conn:
        try:
            return resolve_entity_reference(conn, entity_id, label="owner").entity_id
        except HTTPException as exc:
            if (
                owner_type_of(entity_id) == "service"
                and exc.status_code == 404
            ):
                slug = entity_id.removeprefix("service:")
                if "_" in slug:
                    alt = f"service:{slug.replace('_', '-')}"
                    try:
                        return resolve_entity_reference(
                            conn, alt, label="owner"
                        ).entity_id
                    except HTTPException as alt_exc:
                        if alt_exc.status_code == 404:
                            return {
                                "error": (
                                    f"owner {entity_id} not found ({exc.detail}); "
                                    f"canonical service ids use hyphens "
                                    f"(try {alt!r}). Create the entity before "
                                    "logging friction against it."
                                ),
                                "status_code": 404,
                            }
                        return {
                            "error": f"owner {entity_id}: {alt_exc.detail}",
                            "status_code": alt_exc.status_code,
                        }
            if exc.status_code == 404:
                seat_resolved = _retry_seat_endpoint_owner(conn, entity_id)
                if seat_resolved is not None:
                    return seat_resolved
            if exc.status_code == 404:
                return {
                    "error": (
                        f"owner {entity_id} not found ({exc.detail}); "
                        "create the entity before logging friction against it."
                    ),
                    "status_code": 404,
                }
            return {
                "error": f"owner {entity_id}: {exc.detail}",
                "status_code": exc.status_code,
            }


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


def _protocol_anchor_satisfied(
    *,
    charter_root: str | None,
    window_index: int | None,
    root_thread: str | None,
    cp_ordinal: int | None,
) -> bool:
    return _charter_variant_complete(
        charter_root=charter_root,
        window_index=window_index,
    ) or _continuity_variant_complete(
        root_thread=root_thread,
        cp_ordinal=cp_ordinal,
    )


def _op_friction(
    owner: str | None = None,
    service: str | None = None,
    category: str | None = None,
    note: str | None = None,
    claim: str | None = None,
    suggestion: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
    root_thread: str | None = None,
    cp_ordinal: int | None = None,
    scoreboard_uri: str | None = None,
    actionable: bool | None = None,
    actionable_false_reason: str | None = None,
    checkpoint_turn: int | None = None,
    evidence_uris: list[str] | str | None = None,
    defer_enqueue: bool | None = None,
    confidence: str | None = None,
    confidence_score: float | None = None,
    related: list[dict[str, Any]] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Log a friction assertion; protocol category requires a complete anchor."""
    if (
        owner is not None
        and service is not None
        and owner_entity_id(owner) != owner_entity_id(service)
    ):
        return {
            "error": "Supply either owner= or service= (back-compat alias), not both with different values."
        }
    owner_arg = owner if owner is not None else service
    # `claim` is the slot name on assert/observe/supersede, so seats reach for it
    # here too. Accept it rather than answer "note is required" to a caller who
    # supplied the note under its sibling-op name — but reject when both differ.
    note, alias_err = resolve_mutually_exclusive_aliases(
        primary=note,
        alias=claim,
        primary_name="note",
        alias_name="claim",
    )
    errors: list[dict[str, Any]] = []
    if alias_err:
        errors.append(alias_err)
    errors.extend(
        collect_missing_required(
            {"owner": owner_arg, "note": note, "category": category},
            message_for={
                "owner": (
                    "owner is required (service:/agent_skill:/ai_agent: entity ID, "
                    "or bare slug -> service:); `service` is accepted as an alias"
                ),
                "note": (
                    "note is required — describe what went wrong; "
                    "`claim` is accepted as an alias"
                ),
                "category": (
                    "category is required — pick one accepted friction category; "
                    "`unclassified` is not a write token"
                ),
            },
            enum_accepted={"category": sorted(_FRICTION_CATEGORIES)},
        )
    )
    if not is_missing(category) and category not in _FRICTION_CATEGORIES:
        errors.append(
            field_error(
                "category",
                f"Invalid category {category!r}.",
                accepted=sorted(_FRICTION_CATEGORIES),
                code="invalid_category",
            )
        )
    if errors:
        return validation_error_response(errors)
    # Feature asks stay on the observation log. Charter provenance must not
    # default them to actionable=true (that auto-mints a todo).
    if category == "feature":
        if actionable is None:
            actionable = False
        if actionable is False and not (actionable_false_reason or "").strip():
            actionable_false_reason = (
                "feature request — observation only; seed a todo via "
                "work-item-seed-path only when work is commissioned"
            )
        if actionable is False and defer_enqueue is None:
            defer_enqueue = True
    conf_resolved = _resolve_friction_confidence(confidence, confidence_score)
    if isinstance(conf_resolved, dict):
        return conf_resolved
    resolved_confidence, resolved_score = conf_resolved
    if category == "protocol" and actionable is not False:
        completeness_err = _validate_anchor_completeness(
            charter_root=charter_root,
            window_index=window_index,
            root_thread=root_thread,
            cp_ordinal=cp_ordinal,
            scoreboard_uri=scoreboard_uri,
            checkpoint_turn=checkpoint_turn,
        )
        if completeness_err:
            return {"error": completeness_err}
        if not _protocol_anchor_satisfied(
            charter_root=charter_root,
            window_index=window_index,
            root_thread=root_thread,
            cp_ordinal=cp_ordinal,
        ):
            return {"error": _PROTOCOL_ANCHOR_REQUIRED_ERROR}
    if ":" in owner_arg and owner_type_of(owner_arg) is None:
        return {
            "error": f"Unsupported owner namespace in {owner_arg!r}. Allowed prefixes: service:, agent_skill:, ai_agent: (or a bare slug -> service:)."
        }
    entity_id = owner_entity_id(owner_arg)
    resolved_owner = _resolve_friction_owner_entity_id(entity_id)
    if isinstance(resolved_owner, dict):
        return resolved_owner
    entity_id = resolved_owner
    prepared_related = None
    if related is not None:
        with cortex_conn() as conn:
            prepared_related, rel_err = prepare_related_edges(
                conn,
                related,
                session_id=session_id,
                agent=agent,
            )
        if rel_err is not None:
            return rel_err
    provenance_attrs, prov_err = _build_friction_provenance_attrs(
        charter_root=charter_root,
        window_index=window_index,
        root_thread=root_thread,
        cp_ordinal=cp_ordinal,
        scoreboard_uri=scoreboard_uri,
        session_id=session_id,
        actionable=actionable,
        actionable_false_reason=actionable_false_reason,
        checkpoint_turn=checkpoint_turn,
        defer_enqueue=defer_enqueue,
    )
    if prov_err:
        return {"error": prov_err}

    # category is non-blank and ∈ _FRICTION_CATEGORIES after the gate above
    claim = f"[{category}] {note}"
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
    try:
        result = _create_assertion_impl(body)
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}
    if "error" not in result:
        anchor_kind = "unanchored"
        if provenance_attrs:
            anchor_kind = _friction_anchor_view(provenance_attrs).get("anchor_kind", "unanchored")
        logger.info("cortex friction: %s/%s — %s", entity_id, category, note[:60])
        record(
            "mcp.cortex.friction.logged",
            owner=entity_id,
            owner_type=owner_type_of(entity_id),
            category=category,
            agent=agent,
            anchor_kind=anchor_kind,
        )
        if prepared_related:
            assert session_id is not None and agent is not None
            result.update(
                write_related_edges(
                    int(result["item"]["id"]),
                    prepared_related,
                    session_id=session_id,
                    agent=agent,
                    seeded_by=agent,
                )
            )
    return result


_op_friction.__doc__ = (
    "Log a friction assertion; "
    f"category ∈ {{{', '.join(sorted(_FRICTION_CATEGORIES))}}}; "
    "protocol requires exactly one anchor unless actionable=false. "
    f"Anchor variants: {PROTOCOL_ANCHOR_VARIANTS}. "
    "``confidence`` is honoured when supplied; omitted → hypothesized/0.5. "
    "Invalid values are rejected — never silently downgraded. "
    "Bare seat addresses (web-anthropic, claude-web, claude.ai, cursor, "
    "cursor-sdk) resolve to ai_agent: when that entity exists; a miss names "
    "real service:/agent_skill: owners and does not ask the caller to mint "
    "a service for the seat. "
    "related=[{assertion_id|to_node, edge_type?, strength?, context?}] creates "
    "session edges from the new assertion in the same call; requires session_id "
    "(cortex_brief) and agent."
)


__all__ = ["_list_assertions_impl", "_op_friction"]
