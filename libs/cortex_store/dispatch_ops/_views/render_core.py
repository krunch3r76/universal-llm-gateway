"""Deterministic core rendering and canonical serialization for derived views."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from ...db import query

_CORE_BEGIN_RE = re.compile(r"<!-- view-core:(?P<section_id>[^:]+):begin -->")
_CORE_END_RE = re.compile(r"<!-- view-core:(?P<section_id>[^:]+):end -->")
_CITATION_RE = re.compile(
    r"\[assertion:\d+\]|cortex://[^\s\]]+|workspaces://[^\s\]]+"
)


def validate_citation_grammar(text: str) -> bool:
    return bool(_CITATION_RE.search(text))


def _max_assertion_id(conn, entity_ids: list[str]) -> int:
    if not entity_ids:
        return 0
    placeholders = ",".join("?" for _ in entity_ids)
    rows = query(
        conn,
        f"""
        SELECT COALESCE(MAX(id), 0) AS max_id
        FROM assertions
        WHERE entity_id IN ({placeholders})
          AND superseded_by IS NULL
        """,
        tuple(entity_ids),
    )
    return int(rows[0]["max_id"]) if rows else 0


def _entity_card(conn, entity_id: str) -> dict[str, Any]:
    rows = query(
        conn,
        "SELECT id, type, name, attributes FROM entities WHERE id = ?",
        (entity_id,),
    )
    if not rows:
        return {"entity_id": entity_id, "missing": True}
    row = rows[0]
    attrs = row.get("attributes")
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs) if attrs else {}
        except json.JSONDecodeError:
            attrs = {}
    if not isinstance(attrs, dict):
        attrs = {}
    return {
        "entity_id": row["id"],
        "type": row["type"],
        "name": row.get("name"),
        "mode": attrs.get("mode"),
        "stage": attrs.get("stage"),
        "ring": attrs.get("ring"),
        "cases": attrs.get("cases", []),
        "imprint_rev": attrs.get("imprint_rev"),
    }


def _assertions_for_entity(conn, entity_id: str, *, pending_only: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT id, claim, valid_from, resolution_status
        FROM assertions
        WHERE entity_id = ?
          AND superseded_by IS NULL
    """
    params: list[Any] = [entity_id]
    if pending_only:
        sql += " AND resolution_status = 'pending'"
    sql += " ORDER BY id"
    return [dict(r) for r in query(conn, sql, tuple(params))]


def _walk_neighbors(conn, root_id: str, hops: int = 1) -> list[str]:
    seen = {root_id}
    frontier = [root_id]
    for _ in range(hops):
        next_frontier: list[str] = []
        for entity_id in frontier:
            rows = query(
                conn,
                """
                SELECT to_entity AS neighbor FROM relationships
                WHERE from_entity = ? AND active = 1
                UNION
                SELECT from_entity AS neighbor FROM relationships
                WHERE to_entity = ? AND active = 1
                """,
                (entity_id, entity_id),
            )
            for row in rows:
                neighbor = row["neighbor"]
                if neighbor not in seen:
                    seen.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return sorted(seen)


def _render_section_rows(
    conn,
    section: dict[str, Any],
    *,
    root_id: str | None,
    snapshot: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], list[str]]:
    derivation = section.get("derivation") or {}
    primitive = derivation.get("primitive")
    params = derivation.get("params") or {}
    rows: list[dict[str, Any]] = []
    cited_ids: list[str] = []

    if primitive == "entity_card" and root_id:
        rows.append(_entity_card(conn, root_id))
    elif primitive == "assertions" and root_id:
        pending = bool(params.get("pending_only"))
        for row in _assertions_for_entity(conn, root_id, pending_only=pending):
            rows.append(
                {
                    "assertion_id": row["id"],
                    "claim": row.get("claim"),
                    "valid_from": row.get("valid_from"),
                    "resolution_status": row.get("resolution_status"),
                }
            )
            cited_ids.append(f"assertion:{row['id']}")
    elif primitive == "walk_subgraph" and root_id:
        hops = int(params.get("hops", 1))
        for entity_id in _walk_neighbors(conn, root_id, hops=hops):
            rows.append(_entity_card(conn, entity_id))
            cited_ids.append(entity_id)
    elif primitive == "entity_selector":
        type_selector = params.get("type_selector") or []
        lifecycle = params.get("lifecycle", "active")
        if type_selector:
            placeholders = ",".join("?" for _ in type_selector)
            sql = f"""
                SELECT id, type, name, attributes
                FROM entities
                WHERE type IN ({placeholders})
            """
            qparams: list[Any] = list(type_selector)
            if lifecycle:
                sql += " AND lifecycle = ?"
                qparams.append(lifecycle)
            sql += " ORDER BY id"
            for row in query(conn, sql, tuple(qparams)):
                attrs = row.get("attributes")
                if isinstance(attrs, str):
                    try:
                        attrs = json.loads(attrs) if attrs else {}
                    except json.JSONDecodeError:
                        attrs = {}
                rows.append(
                    {
                        "entity_id": row["id"],
                        "name": row.get("name"),
                        "stage": (attrs or {}).get("stage"),
                        "status": (attrs or {}).get("status"),
                    }
                )
                cited_ids.append(row["id"])
    elif primitive == "deadlines":
        rows.append({"deadlines": []})
    elif primitive == "doctrine_rows":
        rows.append({"doctrine": params.get("kind", "row")})

    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return canonical, rows, cited_ids


def render_core_sections(
    conn,
    recipe: dict[str, Any],
    *,
    root_id: str | None,
    snapshot: dict[str, Any],
    section_ids: set[str] | None = None,
) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for section in sorted(recipe.get("sections", []), key=lambda s: s.get("order", 0)):
        if not section.get("core", True):
            continue
        section_id = section["section_id"]
        if section_ids is not None and section_id not in section_ids:
            continue
        text, _, _ = _render_section_rows(conn, section, root_id=root_id, snapshot=snapshot)
        rendered[section_id] = text
    return rendered


def canonical_core_bytes(core_sections: dict[str, str]) -> bytes:
    ordered = {k: core_sections[k] for k in sorted(core_sections)}
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"))
    return payload.encode("utf-8") + b"\n"


def compute_core_hash(core_sections: dict[str, str]) -> str:
    digest = hashlib.sha256(canonical_core_bytes(core_sections)).hexdigest()
    return f"sha256:{digest}"


def build_document_body(
    stamp: dict[str, Any],
    core_sections: dict[str, str],
    narrative_sections: dict[str, str],
) -> str:
    lines = [f"<!-- view-stamp: {json.dumps(stamp, sort_keys=True)} -->", ""]
    for section_id in sorted(core_sections):
        lines.append(f"<!-- view-core:{section_id}:begin -->")
        lines.append(core_sections[section_id])
        lines.append(f"<!-- view-core:{section_id}:end -->")
        lines.append("")
    for section_id in sorted(narrative_sections):
        lines.append(f"## {section_id}")
        lines.append(narrative_sections[section_id])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def extract_core_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        begin = _CORE_BEGIN_RE.match(line.strip())
        end = _CORE_END_RE.match(line.strip())
        if begin:
            current = begin.group("section_id")
            buf = []
            continue
        if end and current == end.group("section_id"):
            sections[current] = "\n".join(buf).strip()
            current = None
            buf = []
            continue
        if current is not None:
            buf.append(line)
    return sections


def snapshot_for_scope(conn, recipe: dict[str, Any], root_id: str | None) -> dict[str, Any]:
    entity_ids: list[str] = []
    if root_id:
        entity_ids.append(root_id)
        entity_ids.extend(_walk_neighbors(conn, root_id, hops=2))
    else:
        for section in recipe.get("sections", []):
            derivation = (section.get("derivation") or {})
            if derivation.get("primitive") == "entity_selector":
                params = derivation.get("params") or {}
                type_selector = params.get("type_selector") or []
                if type_selector:
                    placeholders = ",".join("?" for _ in type_selector)
                    rows = query(
                        conn,
                        f"SELECT id FROM entities WHERE type IN ({placeholders})",
                        tuple(type_selector),
                    )
                    entity_ids.extend(row["id"] for row in rows)
    max_id = _max_assertion_id(conn, sorted(set(entity_ids)))
    return {
        "max_assertion_id": max_id,
        "as_of": datetime.now(UTC).isoformat(),
    }


__all__ = [
    "build_document_body",
    "canonical_core_bytes",
    "compute_core_hash",
    "extract_core_sections",
    "render_core_sections",
    "snapshot_for_scope",
    "validate_citation_grammar",
]
