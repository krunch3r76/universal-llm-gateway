"""Relationship ops — list, create, update, and soft-delete."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from ..db import cortex_conn
from ..entity_aliases import resolve_entity_reference
from ..routes.relationships import (
    _create_relationship_impl,
    _delete_relationship_impl,
    _list_relationships_impl,
    _update_relationship_impl,
)
from ._shared import record

logger = logging.getLogger("cortex-api.dispatch_ops.relationships")


def _op_relationships(
    entity_id: str | None = None,
    type_id: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    return _list_relationships_impl(
        entity_id=entity_id, type_id=type_id, limit=limit or 50
    )


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
    session_id: str | None = None,
    agent: str | None = None,
    resolve_aliases: bool = True,
    **_: object,
) -> dict[str, Any]:
    for field, val in [
        ("source_id", source_id),
        ("target_id", target_id),
        ("type_id", type_id),
    ]:
        if not val:
            return {"error": f"{field} is required"}
    resolved_aliases: list[dict[str, str]] = []
    try:
        with cortex_conn() as conn:
            resolved_source = resolve_entity_reference(
                conn, source_id, resolve_aliases=resolve_aliases, label="source"
            )
            resolved_target = resolve_entity_reference(
                conn, target_id, resolve_aliases=resolve_aliases, label="target"
            )
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}
    for resolved in [resolved_source.resolved_alias, resolved_target.resolved_alias]:
        if resolved:
            resolved_aliases.append(resolved)

    body: dict[str, Any] = {
        "source_id": resolved_source.entity_id,
        "target_id": resolved_target.entity_id,
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
        ("session_id", session_id),
        ("agent", agent),
    ]:
        if val is not None:
            body[key] = val
    result = _create_relationship_impl(body)
    if resolved_aliases and "error" not in result:
        result["resolved_aliases"] = resolved_aliases
    if "error" not in result:
        logger.info(
            "cortex relationship_create: %s -[%s]-> %s",
            resolved_source.entity_id,
            type_id,
            resolved_target.entity_id,
        )
        record(
            "mcp.cortex.relationship.created",
            source_id=resolved_source.entity_id,
            target_id=resolved_target.entity_id,
            type_id=type_id,
        )
    return result


def _op_relationship_delete(
    relationship_id: int | None = None,
    **_: object,
) -> dict[str, Any]:
    if relationship_id is None:
        return {"error": "relationship_id is required"}
    result = _delete_relationship_impl(int(relationship_id))
    if "error" not in result:
        logger.info("cortex relationship_delete: id=%d", relationship_id)
        record("mcp.cortex.relationship.deleted", relationship_id=relationship_id)
    return result


def _op_relationship_update(
    relationship_id: int | None = None,
    role: str | None = None,
    strength: float | None = None,
    evidence: str | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
    source_uri: str | None = None,
    session_id: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    if relationship_id is None:
        return {"error": "relationship_id is required"}
    body: dict[str, Any] = {}
    for key, val in [
        ("role", role),
        ("strength", strength),
        ("evidence", evidence),
        ("valid_from", valid_from),
        ("valid_until", valid_until),
        ("source_uri", source_uri),
        ("session_id", session_id),
        ("agent", agent),
    ]:
        if val is not None:
            body[key] = val
    result = _update_relationship_impl(int(relationship_id), body)
    if "error" not in result:
        logger.info(
            "cortex relationship_update: id=%d (fields: %s)",
            relationship_id,
            list(body.keys()),
        )
        record(
            "mcp.cortex.relationship.updated",
            relationship_id=relationship_id,
        )
    return result
