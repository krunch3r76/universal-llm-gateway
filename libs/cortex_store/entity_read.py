"""Entity read path — ``get_entity_impl`` for the full EntityDetail projection.

Split out of ``entity_crud.py`` so that module stays under the per-file SLOC
cap. The Card v0 projection lives in ``card.py``; this module handles the
legacy ``intent="full"`` shape (assertions + relationships + reasoning edges
+ compaction projection).
"""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException, status
from universal_logging import get_logger

from .action_hints import detect_expired_unresolved
from .assertion_deserialize_telemetry import (
    assertion_deserialize_skip_reason,
    emit_assertion_deserialize_skipped,
)
from .compaction import apply_compaction_filter
from .db import decode_row, query
from .handoff_surface import apply_handoff_read_projection
from .models import (
    AssertionItem,
    CompactionProjection,
    EdgeItem,
    EntityDetail,
    RelationshipItem,
)
from .relationship_sql import FROM_CLAUSE, SELECT_COLUMNS
from .routes.assertions import _ASSERTION_COLS
from .routes.edges import _EDGE_COLS
from .status_trait_read import apply_option_c_read_projection

logger = get_logger("cortex-api.entity_read")

ASSERTION_JSON_FIELDS = frozenset({"evidence_uris"})
ENTITY_JSON_FIELDS = frozenset({"aliases", "attributes"})


def get_entity_impl(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    include_edges: bool = False,
    edge_limit: int = 20,
    source: str = "agent",
    agent: str = "web",
    session_id: str | None = None,
    include_compaction_pointers: bool = False,
) -> dict[str, object]:
    entities = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not entities:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity not found: {entity_id}",
        )
    entity = entities[0]

    assertion_rows = query(
        conn,
        f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
        "ORDER BY created_at DESC",
        (entity_id,),
    )

    rel_rows = query(
        conn,
        f"SELECT {SELECT_COLUMNS} {FROM_CLAUSE} "
        "WHERE (r.from_entity = ? OR r.to_entity = ?) AND r.active = 1 "
        "ORDER BY r.created_at DESC",
        (entity_id, entity_id),
    )

    edge_rows: list[dict] = []
    if include_edges:
        edge_rows = query(
            conn,
            f"SELECT {_EDGE_COLS} FROM session_edges "
            "WHERE (from_node = ? OR to_node = ?) "
            "AND valid_until IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (entity_id, entity_id, edge_limit),
        )

    if source != "boot":
        try:
            conn.execute(
                "INSERT INTO entity_access_log "
                "(entity_id, agent, operation, source, session_id) "
                "VALUES (?, ?, 'entity_get', ?, ?)",
                (entity_id, agent, source, session_id),
            )
            conn.commit()
        except Exception:
            logger.warning("Access log insert failed for %s", entity_id)

    assertions: list[AssertionItem] = []
    for row in assertion_rows:
        try:
            assertions.append(AssertionItem(**decode_row(row, ASSERTION_JSON_FIELDS)))
        except Exception as exc:
            logger.error(
                "Skipping assertion %s for entity %s — deserialization failed",
                row.get("id"),
                entity_id,
                exc_info=True,
            )
            emit_assertion_deserialize_skipped(
                entity_id=entity_id,
                assertion_id=row.get("id"),
                reason=assertion_deserialize_skip_reason(exc),
            )

    # §6.10 compaction-aware projection (Tier 0 — deterministic, no model)
    compaction_projection: CompactionProjection | None = None
    raw_dicts = [a.model_dump(mode="json") for a in assertions]
    archives_to_children: list[str] | None = None
    try:
        arc_rows = query(
            conn,
            "SELECT to_entity FROM relationships "
            "WHERE from_entity = ? AND type = 'archives_to' AND active = 1",
            (entity_id,),
        )
        archives_to_children = [r["to_entity"] for r in arc_rows]
    except Exception:
        logger.warning("archives_to lookup failed for %s", entity_id)
    projected_dicts, proj_meta = apply_compaction_filter(
        raw_dicts,
        include_compaction_pointers=include_compaction_pointers,
        archives_to_children=archives_to_children,
    )
    if proj_meta is not None:
        assertions = [AssertionItem(**d) for d in projected_dicts]
        compaction_projection = CompactionProjection(**proj_meta)

    relationships = [RelationshipItem(**row) for row in rel_rows]
    edges = [EdgeItem(**row) for row in edge_rows]
    hints = detect_expired_unresolved([a.model_dump() for a in assertions])
    detail_row = apply_option_c_read_projection(decode_row(entity, ENTITY_JSON_FIELDS))
    detail_row, hints = apply_handoff_read_projection(
        detail_row,
        existing_hints=hints or None,
    )
    return EntityDetail(
        **detail_row,
        assertions=assertions,
        relationships=relationships,
        reasoning_edges=edges,
        action_hints=hints,
        compaction_projection=compaction_projection,
    ).model_dump(mode="json")
