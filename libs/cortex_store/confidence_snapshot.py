"""Load a deterministic graph snapshot for confidence derivation (§2, §9).

Reads the eligible slice of the cortex graph into plain in-memory dataclasses so
``confidence_derivation`` can build the propagation operator without re-touching
SQLite. Three substrates are reconciled here (mirrors ``edge_walk`` active
predicates):

  * ``entities``         — nodes; ``confidence_band`` (stored baseline), ``confidence_field``
                           (scope), ``source_uri`` host (echo-collapse key, §7).
  * ``assertions``       — eligible (§2) source-citing rows → priors b (§5) + the
                           confirmed-evidence gate input (§12).
  * ``relationships`` / ``session_edges`` — active signed edges whose BOTH
                           endpoints are eligible entities (§2 drops the rest).

Determinism (§9): all collections are returned sorted by stable id so the caller
builds an identical matrix on identical inputs.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .confidence_field import DEFAULT_CONFIDENCE_FIELD
from .confidence_policy import (
    ALL_SIGNED_EDGE_TYPES,
    SIGN_MAP,
    confidence_weight,
    credibility_for_keys,
    effective_psi,
    is_eligible_review_status,
    normalized_source_key,
    psi_for_derivation,
)
from .db import json_decode, query, table_exists


@dataclass(frozen=True)
class EntityNode:
    entity_id: str
    entity_type: str
    stored_confidence_band: str | None
    confidence_field: str
    source_key: str | None  # provenance host of source_uri (echo-collapse, §7)


@dataclass(frozen=True)
class SourceAssertion:
    """A single eligible source-citing assertion's contribution to one entity."""

    entity_id: str
    source_keys: tuple[str, ...]  # normalized hosts; one assertion joins each
    seeded_by: str | None
    psi_band: str
    psi: float
    confidence: str | None
    c: float


@dataclass(frozen=True)
class SignedEdge:
    src: str  # influencer (evidence/contradiction source)
    tgt: str  # influenced entity
    sign: int  # +1 / −1
    weight: float


@dataclass
class GraphSnapshot:
    entities: dict[str, EntityNode]
    source_assertions: list[SourceAssertion]
    edges: list[SignedEdge]
    null_credibility_count: int = 0
    meta: dict[str, int] = field(default_factory=dict)


def _entity_has_column(conn: sqlite3.Connection, column: str) -> bool:
    rows = conn.execute("PRAGMA table_info(entities)").fetchall()
    return any(row[1] == column for row in rows)


def _load_confidence_fields(conn: sqlite3.Connection) -> dict[str, str]:
    """type → confidence_field, defaulting unknown types to ``confidence_band``."""
    if not table_exists(conn, "type_confidence_fields"):
        return {}
    rows = query(
        conn, "SELECT entity_type, confidence_field FROM type_confidence_fields"
    )
    return {r["entity_type"]: r["confidence_field"] for r in rows}


def _load_entities(conn: sqlite3.Connection) -> dict[str, EntityNode]:
    has_source = _entity_has_column(conn, "source_uri")
    cols = "id, type, confidence_band" + (", source_uri" if has_source else "")
    rows = query(conn, f"SELECT {cols} FROM entities ORDER BY id")
    field_map = _load_confidence_fields(conn)
    nodes: dict[str, EntityNode] = {}
    for r in rows:
        source_uri = r.get("source_uri") if has_source else None
        band = r.get("confidence_band")
        nodes[r["id"]] = EntityNode(
            entity_id=r["id"],
            entity_type=r["type"],
            stored_confidence_band=str(band) if band is not None else None,
            confidence_field=field_map.get(r["type"], DEFAULT_CONFIDENCE_FIELD),
            source_key=normalized_source_key(source_uri) if source_uri else None,
        )
    return nodes


def _load_source_assertions(
    conn: sqlite3.Connection, entity_ids: set[str]
) -> tuple[list[SourceAssertion], int]:
    """Eligible (§2) source-citing assertions grouped per entity, plus a NULL-Ψ count.

    Source-citing (v2) ⇔ ``evidence_uris`` non-empty (N2 external) OR an
    internal-trust ``derivation_type`` (§3b — operator/runtime provenance
    self-sources without a citation URI). Eligibility excludes superseded and
    non-committed review states. Returns rows sorted by entity id then source
    keys for determinism.
    """
    if not table_exists(conn, "assertions"):
        return [], 0
    rows = query(
        conn,
        "SELECT entity_id, confidence, credibility, evidence_uris, seeded_by, "
        "derivation_type, review_status FROM assertions "
        "WHERE superseded_by IS NULL ORDER BY id",
    )
    out: list[SourceAssertion] = []
    null_credibility = 0
    for r in rows:
        entity_id = r.get("entity_id")
        if entity_id not in entity_ids:
            continue
        if not is_eligible_review_status(r.get("review_status")):
            continue
        derivation_type = r.get("derivation_type")
        seeded_by = r.get("seeded_by")
        uris = json_decode(r.get("evidence_uris"), fallback=[]) or []
        keys = (
            tuple(sorted({normalized_source_key(str(u)) for u in uris if u}))
            if isinstance(uris, list) and uris
            else ()
        )
        if not keys:
            # No external citation — self-source iff an internal-trust derivation
            # type (§3b). Anything else still contributes 0 to the prior (§8).
            if psi_for_derivation(derivation_type) is None:
                continue
            keys = (f"internal:{seeded_by or entity_id}",)
        credibility = r.get("credibility")
        if credibility is None:
            null_credibility += 1
            # §3c (option b): derive credibility from the citation host when the
            # stored value is NULL (*.gov ⇒ authority + manual list; else unrated).
            credibility = credibility_for_keys(keys)
        psi_band, psi = effective_psi(credibility, derivation_type)
        out.append(
            SourceAssertion(
                entity_id=entity_id,
                source_keys=keys,
                seeded_by=seeded_by,
                psi_band=psi_band,
                psi=psi,
                confidence=r.get("confidence"),
                c=confidence_weight(r.get("confidence")),
            )
        )
    out.sort(key=lambda a: (a.entity_id, a.source_keys))
    return out, null_credibility


def _load_signed_edges(
    conn: sqlite3.Connection, entity_ids: set[str]
) -> list[SignedEdge]:
    """Active signed edges with both endpoints eligible entities (§2, §7 direction).

    ``relationships`` (structural) active ⇔ ``active = 1 AND valid_until IS NULL``;
    ``session_edges`` (reasoning) active ⇔ ``valid_until IS NULL``. Both substrates
    are queried for every signed type — a type absent from a substrate just yields
    no rows. Direction: edge (src→tgt) means src influences tgt ⇒ row index tgt.
    """
    placeholders = ",".join("?" for _ in ALL_SIGNED_EDGE_TYPES)
    types = tuple(ALL_SIGNED_EDGE_TYPES)
    raw: list[dict] = []
    if table_exists(conn, "relationships"):
        raw += query(
            conn,
            f"SELECT from_entity AS src, to_entity AS tgt, type AS etype, strength "
            f"FROM relationships WHERE type IN ({placeholders}) "
            f"AND active = 1 AND valid_until IS NULL ORDER BY from_entity, to_entity, type",
            types,
        )
    if table_exists(conn, "session_edges"):
        raw += query(
            conn,
            f"SELECT from_node AS src, to_node AS tgt, edge_type AS etype, strength "
            f"FROM session_edges WHERE edge_type IN ({placeholders}) "
            f"AND valid_until IS NULL ORDER BY from_node, to_node, edge_type",
            types,
        )
    edges: list[SignedEdge] = []
    for r in raw:
        src, tgt = r.get("src"), r.get("tgt")
        if src not in entity_ids or tgt not in entity_ids:
            continue  # §2: drop edges touching non-entity / ineligible nodes
        sign = SIGN_MAP.get(r.get("etype"), 0)
        if sign == 0:
            continue
        strength = r.get("strength")
        weight = float(strength) if strength is not None else 1.0
        edges.append(SignedEdge(src=src, tgt=tgt, sign=sign, weight=weight))
    edges.sort(key=lambda e: (e.tgt, e.src, e.sign))
    return edges


def load_snapshot(conn: sqlite3.Connection) -> GraphSnapshot:
    """Build a deterministic ``GraphSnapshot`` from the live cortex DB."""
    entities = _load_entities(conn)
    entity_ids = set(entities)
    source_assertions, null_credibility = _load_source_assertions(conn, entity_ids)
    edges = _load_signed_edges(conn, entity_ids)
    return GraphSnapshot(
        entities=entities,
        source_assertions=source_assertions,
        edges=edges,
        null_credibility_count=null_credibility,
        meta={
            "entity_count": len(entities),
            "source_assertion_count": len(source_assertions),
            "edge_count": len(edges),
        },
    )


__all__ = [
    "EntityNode",
    "GraphSnapshot",
    "SignedEdge",
    "SourceAssertion",
    "load_snapshot",
]
