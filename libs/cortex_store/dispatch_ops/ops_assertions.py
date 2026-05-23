"""Assertion ops — list/create/update/supersede/search/graph helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from ..models import ImpactAnalysisRequest
from ..routes.assertions import (
    _create_assertion_impl,
    _list_assertions_impl,
    _search_assertions_impl,
    _supersede_assertion_impl,
    _update_assertion_impl,
)
from ..routes.graph import activate, analyze_impact_semantic
from ..routes.triage import AgeStagedRequest, age_staged
from ._shared import (
    _DEFAULT_USER_ENTITY,
    _FRICTION_CATEGORIES,
    _VALID_CONFIDENCE,
    record,
)
from .ops_entities import _op_entities


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


def _op_friction_close(
    assertion_id: int | None = None, resolution_kind: str | None = None, **_: object
) -> dict[str, Any]:
    """Stub for F5 friction_close — validates inputs but performs no write.

    TODO: wire actual delegation — supersede(assertion_id) + relationship_create("resolves").
    Until wired, no write is performed; callers must not treat a success response
    as confirmation of any state change.
    """
    if not assertion_id:
        return {"error": "assertion_id required for friction_close"}
    if not resolution_kind or resolution_kind not in {
        "agent_skill:slug",
        "workflow:slug",
        "todo:slug",
        "superseded",
        "wontfix",
    }:
        return {
            "error": f"Invalid resolution_kind={resolution_kind}. Must be one of: agent_skill:slug, workflow:slug, todo:slug, superseded, wontfix"
        }

    # Delegate to existing impls (REST-first, no direct DB)
    # TODO: wire actual delegation — supersede(assertion_id) + relationship_create("resolves")
    # Until wired, this is a stub: no write is performed. Do not treat a success response as confirmation.
    return {
        "status": "stub",
        "message": f"friction_close for {assertion_id} with resolution_kind={resolution_kind} is not yet wired — no write performed.",
        "resolution": resolution_kind,
        "_next": "entity_get on the friction assertion to verify resolves edge; update cortex-deep-ref.mdc if protocol changes",
    }


def _op_age_staged(
    dry_run: bool = True,
    commit_days: int = 30,
    reject_days: int = 90,
    limit: int = 100,
    **_: object,
) -> dict[str, Any]:
    """F3 age-staged op — thin relay to the triage route implementation."""
    return age_staged(
        AgeStagedRequest(
            dry_run=dry_run,
            commit_days=commit_days,
            reject_days=reject_days,
            limit=limit,
        )
    )


logger = get_logger("cortex-api.dispatch_ops.assertions")


def _op_assertions(
    entity_id: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    return _list_assertions_impl(
        entity_id=entity_id,
        confidence=confidence,
        review_status=review_status,
        superseded=superseded,
        limit=limit or 50,
        include_compaction_pointers=include_compaction_pointers,
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
    force: bool = False,
    supersedes_id: int | None = None,
    acknowledge_audit_gaps: list[str] | None = None,
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
    ]:
        if val is not None:
            body[key] = val
    if force:
        body["force"] = True
    if supersedes_id is not None:
        body["supersedes_id"] = supersedes_id
    if acknowledge_audit_gaps is not None:
        body["acknowledge_audit_gaps"] = acknowledge_audit_gaps
    if derivation_type is None or confidence_score is None:
        logger.warning(
            "cortex assert: missing derivation_type=%s or confidence_score=%s — "
            "these will become mandatory in a future version",
            derivation_type,
            confidence_score,
        )
    result = _create_assertion_impl(body)
    if "error" not in result:
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
    service: str | None = None,
    category: str | None = None,
    note: str | None = None,
    suggestion: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not service:
        return {"error": "service is required (e.g. 'mcp-server', 'cortex-api')"}
    if not note:
        return {"error": "note is required — describe what went wrong"}
    if category and category not in _FRICTION_CATEGORIES:
        return {
            "error": f"Invalid category {category!r}. Must be one of: {sorted(_FRICTION_CATEGORIES)}"
        }
    claim = f"[{category or 'unclassified'}] {note}"
    if suggestion:
        claim += f" — Suggestion: {suggestion}"
    body: dict[str, Any] = {
        "entity_id": f"service:{service}",
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
        logger.info("cortex friction: %s/%s — %s", service, category, note[:60])
        record(
            "mcp.cortex.friction.logged",
            service=service,
            category=category or "unclassified",
            agent=agent,
        )
    return result


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


_UNSET: Any = object()
"""Sentinel for nullable fields where None is a meaningful clearing value
distinct from "argument absent". See _op_assertion_update.predicate_form."""


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


def _op_search(
    query: str | None = None,
    limit: int | None = None,
    superseded: bool | None = None,
    entity_type: str | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    if not query:
        return {"error": "query is required"}
    return _search_assertions_impl(
        q=query,
        superseded=bool(superseded),
        entity_type=entity_type,
        limit=limit or 20,
        include_compaction_pointers=include_compaction_pointers,
    )


def _op_analyze_impact(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    if not claim:
        return {"error": "claim is required"}
    if confidence is not None and confidence not in _VALID_CONFIDENCE:
        return {
            "error": f"Invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
        }
    data = analyze_impact_semantic(
        ImpactAnalysisRequest(entity_id=entity_id, claim=claim, confidence=confidence)
    )
    return data.model_dump(mode="json")


def _op_activate(
    entity_ids: list[str] | None = None,
    depth: int | None = None,
    max_results: int | None = None,
    exclude_ids: list[int] | None = None,
    suppress_hubs: bool | None = None,
    decay_factor: float | None = None,
    **_: object,
) -> dict[str, Any]:
    if not entity_ids:
        return {"error": "entity_ids is required (list of seed entity IDs)"}
    return activate(
        entity_ids=",".join(entity_ids),
        depth=depth or 1,
        max_results=max_results or 20,
        exclude_ids=",".join(str(i) for i in exclude_ids) if exclude_ids else None,
        suppress_hubs=True if suppress_hubs is None else suppress_hubs,
        decay_factor=0.5 if decay_factor is None else decay_factor,
    )


def _op_review_queue(
    limit: int | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    lim = limit or 30
    # todo:cortex-aggregate-compaction-filter — these are aggregate (no
    # entity_id) reads; pointer rows are filtered by `list_assertions` itself
    # unless the override is requested.
    flagged_resp = _list_assertions_impl(
        review_status="flagged",
        superseded=False,
        limit=lim,
        include_compaction_pointers=include_compaction_pointers,
    )
    staged_resp = _list_assertions_impl(
        review_status="staged",
        superseded=False,
        limit=lim,
        include_compaction_pointers=include_compaction_pointers,
    )
    low_conf_resp = _list_assertions_impl(
        superseded=False,
        limit=lim,
        include_compaction_pointers=include_compaction_pointers,
    )
    entities = _op_entities(limit=lim)
    flagged = (
        [
            {**a, "priority": 2, "reason": "flagged"}
            for a in flagged_resp.get("items", [])
        ]
        if not flagged_resp.get("error")
        else []
    )
    staged = (
        [
            {**a, "priority": 1, "reason": "staged (quality warning)"}
            for a in staged_resp.get("items", [])
        ]
        if not staged_resp.get("error")
        else []
    )

    low_conf = []
    if not low_conf_resp.get("error"):
        for a in low_conf_resp.get("items", []):
            if a.get("confidence") in ("suspected", "hypothesized"):
                low_conf.append({**a, "priority": 3, "reason": "low_confidence"})

    provisional = []
    thin_descriptions = []
    if not entities.get("error"):
        for e in entities.get("items", []):
            if e.get("status") == "provisional":
                provisional.append({**e, "priority": 4, "reason": "provisional"})
            desc = e.get("description") or ""
            if len(desc) < 50:
                thin_descriptions.append(
                    {**e, "priority": 5, "reason": "thin_description"}
                )

    total = (
        len(flagged)
        + len(staged)
        + len(provisional)
        + len(low_conf)
        + len(thin_descriptions)
    )
    return {
        "provisional_entities": provisional,
        "flagged_assertions": flagged,
        "staged_assertions": staged,
        "low_confidence_assertions": low_conf,
        "thin_descriptions": thin_descriptions,
        "total": total,
    }


