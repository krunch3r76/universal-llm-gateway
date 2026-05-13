"""Bulk relationship upsert dispatch op."""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from fastapi import HTTPException, status

from ..db import cortex_conn, query
from ..entity_aliases import resolve_entity_reference
from ..models import RelationshipCreate, RelationshipItem
from ..routes.relationships import _FROM, _SELECT, SYMMETRIC_REL_TYPES
from ._shared import record
from .ops_bulk_entities import _error_response, _validate_if_exists

_REL_MUTABLE = frozenset(
    {
        "role",
        "strength",
        "evidence",
        "chunk_id",
        "valid_from",
        "valid_until",
        "source_uri",
        "session_id",
        "agent",
    }
)


def _canonical_relationship_identity(
    source_id: str,
    target_id: str,
    type_id: str,
) -> tuple[str, str, str]:
    if type_id in SYMMETRIC_REL_TYPES:
        return (*sorted((source_id, target_id)), type_id)
    return source_id, target_id, type_id


def _existing_relationship(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    target_id: str,
    type_id: str,
) -> dict[str, Any] | None:
    rows = query(
        conn,
        f"SELECT {_SELECT} {_FROM} "
        "WHERE r.from_entity = ? AND r.to_entity = ? AND r.type = ? AND r.active = 1",
        (source_id, target_id, type_id),
    )
    return rows[0] if rows else None


def _relationship_updates_changed(
    existing: dict[str, Any],
    updates: dict[str, object],
) -> bool:
    return any(existing.get(key) != value for key, value in updates.items())


def _insert_relationship(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    source_id: str,
    target_id: str,
    type_id: str,
) -> dict[str, Any]:
    body = RelationshipCreate.model_validate(
        {**payload, "source_id": source_id, "target_id": target_id, "type_id": type_id}
    )
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO relationships "
        "(type, from_entity, to_entity, role, strength, evidence, chunk_id, "
        " valid_from, valid_until, source_uri, session_id, agent, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            type_id,
            source_id,
            target_id,
            body.role,
            body.strength if body.strength is not None else 1.0,
            body.evidence,
            body.chunk_id,
            body.valid_from,
            body.valid_until,
            body.source_uri,
            body.session_id,
            body.agent,
            now,
            now,
        ),
    )
    rows = query(conn, f"SELECT {_SELECT} {_FROM} WHERE r.id = ?", (cur.lastrowid,))
    if not rows:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Relationship created but could not be read back",
        )
    return RelationshipItem(**rows[0]).model_dump(mode="json")


def _update_relationship(
    conn: sqlite3.Connection,
    relationship_id: int,
    updates: dict[str, object],
) -> dict[str, Any]:
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    params = [*updates.values(), now, relationship_id]
    conn.execute(
        f"UPDATE relationships SET {set_clause}, updated_at = ? WHERE id = ?",
        params,
    )
    rows = query(conn, f"SELECT {_SELECT} {_FROM} WHERE r.id = ?", (relationship_id,))
    if not rows:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Relationship updated but could not be read back",
        )
    return RelationshipItem(**rows[0]).model_dump(mode="json")


def _relationship_payload(item: dict[str, Any]) -> dict[str, Any]:
    for field in ["source_id", "target_id", "type_id"]:
        if not item.get(field):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} is required"
            )
    return {
        key: value
        for key, value in item.items()
        if key not in {"if_exists", "resolve_aliases"}
    }


def _bulk_upsert_relationship(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    *,
    if_exists: str,
    resolve_aliases: bool,
) -> dict[str, Any]:
    payload = _relationship_payload(item)
    source = resolve_entity_reference(
        conn, str(payload["source_id"]), resolve_aliases=resolve_aliases, label="source"
    )
    target = resolve_entity_reference(
        conn, str(payload["target_id"]), resolve_aliases=resolve_aliases, label="target"
    )
    type_id = str(payload["type_id"])
    if not query(
        conn, "SELECT type FROM relationship_types WHERE type = ?", (type_id,)
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Relationship type not found: {type_id}",
        )

    source_id, target_id, type_id = _canonical_relationship_identity(
        source.entity_id, target.entity_id, type_id
    )
    existing = _existing_relationship(
        conn, source_id=source_id, target_id=target_id, type_id=type_id
    )
    resolved_aliases = [
        alias for alias in [source.resolved_alias, target.resolved_alias] if alias
    ]
    base = {
        "source_id": source_id,
        "target_id": target_id,
        "type_id": type_id,
        **({"resolved_aliases": resolved_aliases} if resolved_aliases else {}),
    }

    if existing is None:
        item_payload = _insert_relationship(
            conn, payload, source_id=source_id, target_id=target_id, type_id=type_id
        )
        return {**base, "relationship_id": item_payload["id"], "action": "created"}
    if if_exists == "fail":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "detail": "Relationship already exists",
                "relationship_id": existing["id"],
            },
        )
    if if_exists == "skip":
        return {**base, "relationship_id": existing["id"], "action": "skipped"}

    updates = {
        key: value
        for key, value in payload.items()
        if key in _REL_MUTABLE and value is not None
    }
    if not updates or not _relationship_updates_changed(existing, updates):
        return {
            **base,
            "relationship_id": existing["id"],
            "action": "skipped",
            "reason": "unchanged",
        }
    item_payload = _update_relationship(conn, int(existing["id"]), updates)
    return {**base, "relationship_id": item_payload["id"], "action": "updated"}


def _op_relationships_bulk_upsert(
    relationships: list[dict[str, Any]] | None = None,
    if_exists: str = "fail",
    resolve_aliases: bool = True,
    **_: object,
) -> dict[str, Any]:
    if not isinstance(relationships, list) or not relationships:
        return {"error": "relationships must be a non-empty list"}
    default_if_exists = _validate_if_exists(if_exists)
    items: list[dict[str, Any]] = []
    with cortex_conn() as conn:
        for index, item in enumerate(relationships):
            if not isinstance(item, dict):
                conn.rollback()
                return {
                    "error": "each relationship must be an object",
                    "operation": "relationships_bulk_upsert",
                    "failed_index": index,
                    "rolled_back": True,
                }
            try:
                item_if_exists = _validate_if_exists(
                    item.get("if_exists", default_if_exists)
                )
                items.append(
                    _bulk_upsert_relationship(
                        conn,
                        item,
                        if_exists=item_if_exists,
                        resolve_aliases=bool(
                            item.get("resolve_aliases", resolve_aliases)
                        ),
                    )
                )
            except HTTPException as exc:
                conn.rollback()
                return _error_response(
                    exc, op="relationships_bulk_upsert", failed_index=index
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                return {
                    "error": str(exc),
                    "operation": "relationships_bulk_upsert",
                    "failed_index": index,
                    "rolled_back": True,
                }
        conn.commit()
    record("mcp.cortex.relationships.bulk_upserted", count=len(items))
    return {
        "items": items,
        "created": sum(1 for item in items if item["action"] == "created"),
        "updated": sum(1 for item in items if item["action"] == "updated"),
        "skipped": sum(1 for item in items if item["action"] == "skipped"),
        "rolled_back": False,
    }
