"""Entity ops — entities, entity_get, entity_create, entity_update."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import HTTPException

from ..db import cortex_conn
from ..routes.entities import (
    _create_entity_impl,
    _get_entity_impl,
    _list_entities_impl,
    _update_entity_impl,
)
from ._shared import _ENTITY_MUTABLE, _VALID_STATUS, _compute_content_hash, record

logger = logging.getLogger("cortex-api.dispatch_ops.entities")


def _op_entities(
    type: str | None = None,
    workflow_state: str | None = None,
    limit: int | None = None,
    **_: object,
) -> dict[str, Any]:
    with cortex_conn() as conn:
        return _list_entities_impl(
            conn, entity_type=type, workflow_state=workflow_state, limit=limit or 50
        )


def _op_entity_get(
    entity_id: str | None = None,
    include_edges: bool = False,
    edge_limit: int = 20,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    with cortex_conn() as conn:
        return _get_entity_impl(
            conn,
            entity_id=entity_id,
            include_edges=include_edges,
            edge_limit=edge_limit,
            include_compaction_pointers=include_compaction_pointers,
        )


def _op_entity_create(
    id: str | None = None,
    type: str | None = None,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
    workflow_state: str | None = None,
    notes: str | None = None,
    aliases: list[str] | None = None,
    attributes: dict[str, Any] | None = None,
    source_uri: str | None = None,
    content_hash: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {"id": id, "type": type, "name": name}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    if status is not None and status not in _VALID_STATUS:
        return {
            "error": f"Invalid status {status!r}. "
            f"Must be one of: {sorted(_VALID_STATUS)}"
        }
    if source_uri is not None and content_hash is None:
        content_hash = _compute_content_hash(source_uri)
    payload: dict[str, Any] = {
        "id": id,
        "type": type,
        "name": name,
        **({} if description is None else {"description": description}),
        **({} if status is None else {"status": status}),
        **({} if workflow_state is None else {"workflow_state": workflow_state}),
        **({} if notes is None else {"notes": notes}),
        **({} if aliases is None else {"aliases": aliases}),
        **({} if attributes is None else {"attributes": attributes}),
        **({} if source_uri is None else {"source_uri": source_uri}),
        **({} if content_hash is None else {"content_hash": content_hash}),
    }
    try:
        with cortex_conn() as conn:
            result = _create_entity_impl(conn, payload)
    except sqlite3.IntegrityError:
        logger.warning("entity_create conflict for id=%s", id)
        try:
            with cortex_conn() as conn:
                existing = _get_entity_impl(conn, entity_id=str(id))
        except HTTPException:
            existing = None
        raise HTTPException(
            status_code=409,
            detail={
                "detail": f"Entity already exists: {id}",
                "existing_entity": existing,
                "retryable": False,
            },
        )
    except sqlite3.OperationalError as exc:
        # Transient sqlite condition (locked, busy, IO error, disk full).
        # Distinct from IntegrityError above (caller error) — this is upstream
        # degradation that the agent should retry rather than treat as a fatal
        # malformed-input signal.
        logger.error(
            "entity_create transient cortex degradation for id=%s: %s", id, exc
        )
        raise HTTPException(
            status_code=503,
            detail={
                "detail": f"Cortex temporarily unavailable: {exc}",
                "retryable": True,
            },
        )
    if "error" not in result:
        logger.info("cortex entity_create: %s (%s)", id, type)
        record("mcp.cortex.entity.created", entity_id=id, entity_type=type)
    return result


def _op_entity_update(
    entity_id: str | None = None,
    **kwargs: object,
) -> dict[str, Any]:
    if not entity_id:
        return {"error": "entity_id is required"}
    updates: dict[str, object] = {
        k: v for k, v in kwargs.items() if k in _ENTITY_MUTABLE
    }
    if (
        "source_uri" in updates
        and updates["source_uri"] is not None
        and "content_hash" not in updates
    ):
        computed = _compute_content_hash(str(updates["source_uri"]))
        if computed:
            updates["content_hash"] = computed
    if not updates:
        return {"error": "No fields to update"}
    with cortex_conn() as conn:
        result = _update_entity_impl(conn, entity_id=entity_id, updates=updates)
    if "error" not in result:
        logger.info("cortex entity_update: %s", entity_id)
    return result
