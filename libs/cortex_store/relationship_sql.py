"""Shared SQL fragments for cortex_store relationship queries.

Holds the SELECT column list, JOIN chain, and symmetric-type set used by
both the FastAPI route handlers and the dispatch ops. Hoisting them here
breaks the cross-module private-symbol coupling between
``libs/cortex_store/dispatch_ops/ops_bulk_relationships.py`` (and
``ops_relationships.py``) and the route module they previously reached
into for ``_FROM``/``_SELECT``.
"""

from __future__ import annotations

SYMMETRIC_REL_TYPES: frozenset[str] = frozenset({"related_to", "co-occurs_with"})


SELECT_COLUMNS = """
    r.id, r.from_entity AS source_id, r.to_entity AS target_id,
    r.type AS type_id, rt.description AS type_name,
    se.name AS source_name, te.name AS target_name,
    r.role, r.strength, r.evidence, r.chunk_id,
    r.valid_from, r.valid_until, r.source_uri,
    r.session_id, r.agent, r.created_at
"""


FROM_CLAUSE = """
    FROM relationships r
    JOIN relationship_types rt ON rt.type = r.type
    LEFT JOIN entities se ON se.id = r.from_entity
    LEFT JOIN entities te ON te.id = r.to_entity
"""
