"""Assertion read ops — list, search, review-queue, impact, activate, age-staged.

Write-path (assert/observe/friction/friction_close) and update-path
(supersede/assertion_update/assertion_get) live in ``ops_assertions_write``
and ``ops_assertions_update`` respectively — split for SLOC budget per
[quality]. This module re-exports their public names so the dispatcher
import surface stays backward-compatible: callers continue to do
``from libs.cortex_store.dispatch_ops.ops_assertions import _op_assert``
without caring about the split.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..db import cortex_conn, query
from ..entity_aliases import resolve_entity_reference
from ..models import ImpactAnalysisRequest
from ..routes.assertions import (
    _list_assertions_impl,
    _search_assertions_impl,
)
from ..routes.graph import activate, analyze_impact_semantic
from ..routes.triage import AgeStagedRequest, age_staged
from ..status_trait_read import effective_confidence_band
from ._shared import _VALID_CONFIDENCE, service_entity_id
from .ops_assertions_update import (
    _op_assertion_get,
    _op_assertion_update,
    _op_supersede,
)
from .ops_assertions_write import (
    _op_assert,
    _op_friction,
    _op_friction_close,
    _op_observe,
)
from .ops_entities import _op_entities, _resolve_read_entity_id

logger = get_logger("cortex-api.dispatch_ops.assertions")


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


def _op_assertion_state(
    entity_id: str | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    """Lightweight ratification/count projection for a single entity."""
    if not entity_id:
        return {"error": "entity_id is required"}
    with cortex_conn() as conn:
        try:
            canonical_id = _resolve_read_entity_id(
                conn,
                entity_id,
                resolve_aliases=resolve_aliases,
                raw_id=raw_id,
            )
        except HTTPException as exc:
            return {"error": exc.detail, "status_code": exc.status_code}
        count_rows = query(
            conn,
            "SELECT COUNT(*) AS confirmed_count FROM assertions "
            "WHERE entity_id = ? AND confidence = 'confirmed' AND superseded_by IS NULL",
            (canonical_id,),
        )
        confirmed_count = int(count_rows[0]["confirmed_count"]) if count_rows else 0
        latest_id: int | None = None
        if confirmed_count > 0:
            latest_rows = query(
                conn,
                "SELECT id FROM assertions "
                "WHERE entity_id = ? AND confidence = 'confirmed' AND superseded_by IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (canonical_id,),
            )
            if latest_rows:
                latest_id = int(latest_rows[0]["id"])
        return {
            "entity_id": canonical_id,
            "ratified": confirmed_count >= 1,
            "confirmed_count": confirmed_count,
            "latest_confirmed_assertion_id": latest_id,
        }


def _op_assertions(
    entity_id: str | None = None,
    entity_id_prefix: str | None = None,
    filter: str | None = None,
    seeded_by: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    intent: str | None = None,
    include_compaction_pointers: bool = False,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    resolved_intent = intent if intent in ("summary", "full") else "summary"
    if entity_id:
        with cortex_conn() as conn:
            try:
                resolved = resolve_entity_reference(
                    conn,
                    entity_id,
                    resolve_aliases=resolve_aliases,
                    raw_id=raw_id,
                )
            except HTTPException as exc:
                return {"error": exc.detail, "status_code": exc.status_code}
        entity_id = resolved.entity_id if not raw_id else entity_id
    result = _list_assertions_impl(
        entity_id=entity_id,
        entity_id_prefix=entity_id_prefix,
        claim_filter=filter,
        seeded_by=seeded_by,
        confidence=confidence,
        review_status=review_status,
        superseded=superseded,
        limit=limit or 50,
        intent=resolved_intent,
        include_compaction_pointers=include_compaction_pointers,
    )
    if not result.get("error") and resolved_intent == "summary":
        result["_next"] = (
            "Deepen one row: cortex(tool=assertion_get, assertion_id=<id>). "
            "Entity context: cortex(tool=entity_get, intent=card). "
            "Full rows: re-call with intent=full."
        )
    return result


def _op_frictions(
    service: str | None = None,
    category: str | None = None,
    seeded_by: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    """List open friction assertions (service:* entities, bracketed category claims)."""
    entity_id = service_entity_id(service) if service else None
    claim_filter = f"[{category}]" if category else None
    result = _list_assertions_impl(
        entity_id=entity_id,
        entity_id_prefix=None if entity_id else "service:",
        claim_filter=claim_filter,
        seeded_by=seeded_by,
        superseded=False if superseded is None else superseded,
        limit=limit or 30,
        intent="full",
        include_compaction_pointers=include_compaction_pointers,
    )
    if not result.get("error"):
        result["_next"] = (
            "Actionable row → codified bug ticket, investigate→execute fix cycle: investigate "
            "(cursor: role=cursor-consult; web: role=web-consult) → dense spec; "
            "execute (cursor: role=cursor-implement against spec; web: inline). "
            "DEFAULT investigate unless mechanical-only or a dense spec exists. "
            "lifecycle investigate→fix→report. friction() is log-only. "
            "Close via friction_close (agent_skill:|workflow:|todo:|superseded|wontfix). "
            "Skill: docs/agent-guides/skills/friction-review.md or consult-routing § Codified bug reports."
        )
    return result


def _op_search(
    query: str | None = None,
    limit: int | None = None,
    superseded: bool | None = None,
    entity_type: str | None = None,
    intent: str | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    if not query:
        return {"error": "query is required"}
    resolved_intent = intent if intent in ("summary", "full") else "summary"
    return _search_assertions_impl(
        q=query,
        superseded=bool(superseded),
        entity_type=entity_type,
        limit=limit or 20,
        intent=resolved_intent,
        include_compaction_pointers=include_compaction_pointers,
    )


def _op_analyze_impact(
    entity_id: str | None = None,
    claim: str | None = None,
    confidence: str | None = None,
    resolve_aliases: bool = True,
    raw_id: bool = False,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    with cortex_conn() as conn:
        try:
            resolved = resolve_entity_reference(
                conn,
                entity_id,
                resolve_aliases=resolve_aliases,
                raw_id=raw_id,
            )
        except HTTPException as exc:
            return {"error": exc.detail, "status_code": exc.status_code}
    entity_id = resolved.entity_id if not raw_id else entity_id
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
        intent="full",
        include_compaction_pointers=include_compaction_pointers,
    )
    staged_resp = _list_assertions_impl(
        review_status="staged",
        superseded=False,
        limit=lim,
        intent="full",
        include_compaction_pointers=include_compaction_pointers,
    )
    low_conf_resp = _list_assertions_impl(
        superseded=False,
        limit=lim,
        intent="full",
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
            band = effective_confidence_band(e)
            if band == "provisional":
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


__all__ = [
    # write-path (re-exported from ops_assertions_write)
    "_op_assert",
    "_op_friction",
    "_op_friction_close",
    "_op_observe",
    # update / supersede / single-get (re-exported from ops_assertions_update)
    "_op_assertion_get",
    "_op_assertion_update",
    "_op_supersede",
    # read-path (defined here)
    "_op_activate",
    "_op_age_staged",
    "_op_analyze_impact",
    "_op_assertion_state",
    "_op_assertions",
    "_op_frictions",
    "_op_review_queue",
    "_op_search",
]
