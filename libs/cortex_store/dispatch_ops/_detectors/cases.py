"""Case-scoped detectors: structural integrity (assertions, relationships,
documents), attribute references to skills, and the case marker-absent
info-level check.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...db import query
from .._shared import _FILES_ROOT
from ._shared import _finding


def detect_case_no_assertions(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Cases with no assertions (basic integrity)."""
    sql = """
        SELECT id, name FROM entities
        WHERE type = 'case'
        AND NOT EXISTS (SELECT 1 FROM assertions WHERE entity_id = entities.id)
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding("case_no_assertions", r["id"], f"Case '{r['name']}' has no assertions")
        for r in rows
    ]


def detect_case_no_relationships(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Cases with no relationships (basic graph connectivity)."""
    sql = """
        SELECT id, name FROM entities
        WHERE type = 'case'
        AND NOT EXISTS (
            SELECT 1 FROM relationships
            WHERE (from_entity = entities.id OR to_entity = entities.id) AND active = 1
        )
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "case_no_relationships",
            r["id"],
            f"Case '{r['name']}' has no relationships",
        )
        for r in rows
    ]


def detect_case_no_documents(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Cases with no wired documents (via evidence_for or inventory_item)."""
    sql = """
        SELECT id, name FROM entities
        WHERE type = 'case'
        AND NOT EXISTS (
            SELECT 1 FROM relationships
            WHERE from_entity = entities.id
            AND type IN ('evidence_for', 'inventory_item')
            AND active = 1
        )
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "case_no_documents",
            r["id"],
            f"Case '{r['name']}' has no wired documents",
        )
        for r in rows
    ]


def detect_document_not_wired_to_case(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Documents that exist but are not wired to any case."""
    sql = """
        SELECT id, name FROM entities
        WHERE type = 'document'
        AND NOT EXISTS (
            SELECT 1 FROM relationships
            WHERE to_entity = entities.id
            AND type IN ('evidence_for', 'inventory_item')
            AND active = 1
        )
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "document_not_wired_to_case",
            r["id"],
            f"Document '{r['name']}' is not wired to any case",
        )
        for r in rows
    ]


def detect_case_attribute_skill_dangling(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Case attributes referencing non-existent agent_skill entities."""
    sql = """
        SELECT id, name, attributes FROM entities
        WHERE type = 'case' AND attributes IS NOT NULL
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    findings = []
    # Collect all skill refs across all rows first, then bulk-check in one query (W1 budget)
    row_skills: list[tuple[dict, list[str]]] = []
    for r in rows:
        try:
            attrs = r.get("attributes")
            if isinstance(attrs, str):
                attrs = json.loads(attrs)
            if not isinstance(attrs, dict):
                continue
            skill_refs = attrs.get("required_skills", []) or attrs.get("skills", [])
            if skill_refs:
                row_skills.append((r, list(skill_refs)))
        except Exception as exc:
            findings.append(
                _finding(
                    "dangling_attribute_reference",
                    r["id"],
                    f"Malformed attributes JSON — could not parse: {exc}",
                )
            )
            continue

    if not row_skills:
        return findings

    # Bulk existence check — one query for all referenced skill entities
    all_skill_entities = {
        f"agent_skill:{skill_id}" for _, refs in row_skills for skill_id in refs
    }
    placeholders = ",".join("?" for _ in all_skill_entities)
    existing_ids = {
        row["id"]
        for row in query(
            conn,
            f"SELECT id FROM entities WHERE id IN ({placeholders})",
            tuple(all_skill_entities),
        )
    }

    for r, skill_refs in row_skills:
        for skill_id in skill_refs:
            if f"agent_skill:{skill_id}" not in existing_ids:
                findings.append(
                    _finding(
                        "case_attribute_skill_dangling",
                        r["id"],
                        f"Case references non-existent skill {skill_id}",
                    )
                )
    return findings


def detect_case_marker_absent(conn, subject: str | None = None) -> list[dict[str, Any]]:
    """Case markdown files missing the CORTEX_GENERATED marker block (info-level)."""
    sql = "SELECT id, source_uri FROM entities WHERE type = 'case' AND source_uri IS NOT NULL"
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    findings = []
    marker_re = re.compile(r"<!--\s*CORTEX_GENERATED", re.IGNORECASE)
    for r in rows:
        uri = r.get("source_uri")
        if not uri:
            continue
        try:
            path = _FILES_ROOT / uri
            if path.is_file() and path.suffix.lower() in (".md", ".markdown"):
                content = path.read_text(encoding="utf-8", errors="ignore")
                if not marker_re.search(content):
                    findings.append(
                        _finding(
                            "case_marker_absent",
                            r["id"],
                            "Case markdown missing CORTEX_GENERATED marker block",
                            audit_id=None,
                        )
                    )
        except Exception:
            continue
    return findings


__all__ = [
    "detect_case_attribute_skill_dangling",
    "detect_case_marker_absent",
    "detect_case_no_assertions",
    "detect_case_no_documents",
    "detect_case_no_relationships",
    "detect_document_not_wired_to_case",
]
