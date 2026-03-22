"""Cortex v2.1 op handlers — assertion lifecycle, relationships, stats, surface forms.

Plain handler functions consumed by the _OPS dispatch table in cortex.py.
These follow the same pattern as the existing _op_* handlers: accept kwargs,
call _cx() for relay, return dict.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from mcp_events import record

from ._cortex_relay import _cx

logger = logging.getLogger(__name__)


def _op_assertion_update(
    assertion_id: int | None = None,
    superseded_by: int | None = None,
    valid_until: str | None = None,
    confidence: str | None = None,
    confidence_score: float | None = None,
    review_status: str | None = None,
    reviewer: str | None = None,
    reviewed_at: str | None = None,
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
        ]
        if val is not None
    }
    if not body:
        return {"error": "No fields to update"}
    result = _cx("PATCH", f"/assertions/{assertion_id}", body)
    if "error" not in result:
        logger.info("cortex assertion_update: %d", assertion_id)
        record("mcp.cortex.assertion.updated", assertion_id=assertion_id)
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
    **_: object,
) -> dict[str, Any]:
    for field, val in [
        ("old_assertion_id", old_assertion_id),
        ("entity_id", entity_id),
        ("claim", claim),
        ("confidence", confidence),
        ("evidence", evidence),
    ]:
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "old_assertion_id": old_assertion_id,
        "entity_id": entity_id,
        "claim": claim,
        "confidence": confidence,
        "evidence": evidence,
    }
    for key, val in [
        ("evidence_uris", evidence_uris),
        ("valid_from", valid_from),
        ("derivation_type", derivation_type),
    ]:
        if val is not None:
            body[key] = val
    result = _cx("POST", "/assertions/supersede", body)
    if "error" not in result:
        new_id = result.get("new", {}).get("id")
        logger.info("cortex supersede: %d -> %s", old_assertion_id, new_id)
        record(
            "mcp.cortex.assertion.superseded",
            old_id=old_assertion_id,
            new_id=new_id,
        )
    return result


def _op_relationships(
    entity_id: str | None = None,
    type_id: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    params: dict[str, object] = {"limit": limit or 50}
    if entity_id is not None:
        params["entity_id"] = entity_id
    if type_id is not None:
        params["type_id"] = type_id
    return _cx("GET", f"/relationships?{urlencode(params)}")


def _op_relationship_create(
    source_id: str | None = None,
    target_id: str | None = None,
    type_id: str | None = None,
    role: str | None = None,
    strength: float | None = None,
    evidence: str | None = None,
    chunk_id: int | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    source_uri: str | None = None,
    **_: object,
) -> dict[str, Any]:
    for field, val in [
        ("source_id", source_id),
        ("target_id", target_id),
        ("type_id", type_id),
    ]:
        if not val:
            return {"error": f"{field} is required"}
    body: dict[str, Any] = {
        "source_id": source_id,
        "target_id": target_id,
        "type_id": type_id,
    }
    for key, val in [
        ("role", role),
        ("strength", strength),
        ("evidence", evidence),
        ("chunk_id", chunk_id),
        ("valid_from", valid_from),
        ("valid_until", valid_until),
        ("source_uri", source_uri),
    ]:
        if val is not None:
            body[key] = val
    result = _cx("POST", "/relationships", body)
    if "error" not in result:
        logger.info(
            "cortex relationship_create: %s -[%s]-> %s",
            source_id,
            type_id,
            target_id,
        )
        record(
            "mcp.cortex.relationship.created",
            source_id=source_id,
            target_id=target_id,
            type_id=type_id,
        )
    return result


def _op_stats(**_: object) -> dict[str, Any]:
    return _cx("GET", "/stats")


def _op_surface_forms(
    entity_id: str | None = None,
    mention: str | None = None,
    mention_type: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    params: dict[str, object] = {"limit": limit or 50}
    if entity_id is not None:
        params["entity_id"] = entity_id
    if mention is not None:
        params["mention"] = mention
    if mention_type is not None:
        params["entity_type_hint"] = mention_type
    return _cx("GET", f"/surface-forms?{urlencode(params)}")
