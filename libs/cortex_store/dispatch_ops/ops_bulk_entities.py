"""Bulk entity upsert dispatch op."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException, status

from ..db import cortex_conn, decode_row, query
from ..entity_id_norm import canonicalize_entity_id
from ..entity_source_uri import stranded_nested_source_uri
from ._shared import (
    _ENTITY_MUTABLE,
    _VALID_STATUS,
    _compute_content_hash,
    record,
    reject_trait_writes_at_create,
)


def _entity_crud():
    # Lazy import — entity_crud transitively imports this package via
    # workflow_state → dispatch_ops/_shared, so resolving these symbols
    # at module import time deadlocks. Defer until first call.
    from ..entity_crud import (
        ENTITY_JSON_FIELDS,
        create_entity_impl,
        update_entity_impl,
    )

    return ENTITY_JSON_FIELDS, create_entity_impl, update_entity_impl


_IF_EXISTS = frozenset({"fail", "update", "skip"})


def _error_response(
    exc: HTTPException,
    *,
    op: str,
    failed_index: int,
) -> dict[str, Any]:
    return {
        "error": exc.detail,
        "status_code": exc.status_code,
        "operation": op,
        "failed_index": failed_index,
        "rolled_back": True,
    }


def _validate_if_exists(value: object) -> str:
    if value not in _IF_EXISTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"if_exists must be one of {sorted(_IF_EXISTS)}, got {value!r}",
        )
    return str(value)


def _entity_updates_changed(
    existing: dict[str, Any],
    updates: dict[str, object],
) -> bool:
    if stranded_nested_source_uri(
        existing.get("attributes"),
        existing.get("source_uri"),
    ):
        return True
    attrs_update = updates.get("attributes")
    if isinstance(attrs_update, dict):
        nested = attrs_update.get("source_uri")
        if isinstance(nested, str) and nested.strip():
            return True
    for key, value in updates.items():
        if key == "attributes" and isinstance(value, dict):
            merged = dict(existing.get("attributes") or {})
            merged.update(value)
            if merged != (existing.get("attributes") or {}):
                return True
        elif existing.get(key) != value:
            return True
    return False


def _entity_payload(item: dict[str, Any]) -> dict[str, Any]:
    required = {
        "id": item.get("id"),
        "type": item.get("type"),
        "name": item.get("name"),
    }
    for field, value in required.items():
        if not value:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} is required"
            )
    if item.get("status") is not None and item["status"] not in _VALID_STATUS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Invalid status {item['status']!r}. Must be one of: {sorted(_VALID_STATUS)}",
        )

    payload = {key: value for key, value in item.items() if key != "if_exists"}
    if payload.get("source_uri") is not None and payload.get("content_hash") is None:
        content_hash = _compute_content_hash(str(payload["source_uri"]))
        if content_hash is not None:
            payload["content_hash"] = content_hash
    return payload


def _bulk_upsert_entity(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    *,
    if_exists: str,
    post_commit_emits: list[Any] | None = None,
) -> dict[str, Any]:
    ENTITY_JSON_FIELDS, create_entity_impl, update_entity_impl = _entity_crud()  # noqa: N806
    payload = _entity_payload(item)
    payload["id"] = canonicalize_entity_id(str(payload["id"]), str(payload["type"]))
    entity_id = str(payload["id"])
    existing_rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not existing_rows:
        trait_error = reject_trait_writes_at_create(payload)
        if trait_error is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                trait_error["error"],
            )
        create_entity_impl(conn, payload, commit=False)
        return {"id": entity_id, "action": "created"}

    existing = decode_row(existing_rows[0], ENTITY_JSON_FIELDS)
    if str(existing["type"]) != str(payload["type"]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "detail": f"Entity {entity_id} already exists with a different type",
                "existing_type": existing["type"],
                "requested_type": payload["type"],
            },
        )
    if if_exists == "fail":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "detail": f"Entity already exists: {entity_id}",
                "existing_entity_id": entity_id,
            },
        )
    if if_exists == "skip":
        return {"id": entity_id, "action": "skipped", "reason": "exists"}

    updates = {key: value for key, value in payload.items() if key in _ENTITY_MUTABLE}
    if not updates or not _entity_updates_changed(existing, updates):
        return {"id": entity_id, "action": "skipped", "reason": "unchanged"}
    if isinstance(updates.get("attributes"), dict):
        merged_attrs = dict(existing.get("attributes") or {})
        merged_attrs.update(updates["attributes"])
        updates["attributes"] = merged_attrs
    update_entity_impl(
        conn,
        entity_id=entity_id,
        updates=updates,
        commit=False,
        post_commit_emits=post_commit_emits,
    )
    return {"id": entity_id, "action": "updated"}


def _op_entities_bulk_upsert(
    entities: list[dict[str, Any]] | None = None,
    if_exists: str = "fail",
    **_: object,
) -> dict[str, Any]:
    if not isinstance(entities, list) or not entities:
        return {"error": "entities must be a non-empty list"}
    default_if_exists = _validate_if_exists(if_exists)
    items: list[dict[str, Any]] = []
    post_commit_emits: list[Any] = []
    with cortex_conn() as conn:
        for index, item in enumerate(entities):
            if not isinstance(item, dict):
                conn.rollback()
                record(
                    "mcp.cortex.bulk.rolled.back",
                    op="entities_bulk_upsert",
                    failed_index=index,
                    reason="item_not_object",
                )
                return {
                    "error": "each entity must be an object",
                    "operation": "entities_bulk_upsert",
                    "failed_index": index,
                    "rolled_back": True,
                }
            try:
                item_if_exists = _validate_if_exists(
                    item.get("if_exists", default_if_exists)
                )
                items.append(
                    _bulk_upsert_entity(
                        conn,
                        item,
                        if_exists=item_if_exists,
                        post_commit_emits=post_commit_emits,
                    )
                )
            except HTTPException as exc:
                conn.rollback()
                record(
                    "mcp.cortex.bulk.rolled.back",
                    op="entities_bulk_upsert",
                    failed_index=index,
                    reason="http_exception",
                    status_code=exc.status_code,
                )
                return _error_response(
                    exc, op="entities_bulk_upsert", failed_index=index
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                record(
                    "mcp.cortex.bulk.rolled.back",
                    op="entities_bulk_upsert",
                    failed_index=index,
                    reason="integrity_error",
                )
                return {
                    "error": str(exc),
                    "operation": "entities_bulk_upsert",
                    "failed_index": index,
                    "rolled_back": True,
                }
        conn.commit()
    # Post-commit emits: workflow_state transitions captured during the loop
    # fire here, after the SQL transaction has actually persisted, so a
    # rolled-back batch does not leave false cortex.todo.closure.gap signals.
    for emit in post_commit_emits:
        emit()
    record("mcp.cortex.entities.bulk.upserted", count=len(items))
    return {
        "items": items,
        "created": sum(1 for item in items if item["action"] == "created"),
        "updated": sum(1 for item in items if item["action"] == "updated"),
        "skipped": sum(1 for item in items if item["action"] == "skipped"),
        "rolled_back": False,
    }
