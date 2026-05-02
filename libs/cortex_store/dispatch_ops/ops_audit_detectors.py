"""Cortex audit detectors — Layer 2 (Phase 1b of cortex-graph-projection-and-audit-primitives per v2 plan at tmp/prompts/cortex-primitives/implementation-plan-v2.md).

Graph-only (10 kinds, SQL-only, <100ms default for session_audit) + 4 fs-touching (opt-in via include_filesystem=true) + 1 info.

Severity per §6 table. Findings shape: {kind, subject, severity, detail, audit_id}.

Uses _shared._validate_canonical_sandbox_path and _FILES_ROOT for fs detectors.

See §8 for budgets, §6 for taxonomy and event emission (kind-in-payload, stable signals).

"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..db import cortex_conn, query
from ._shared import _FILES_ROOT, _validate_canonical_sandbox_path

# Gap taxonomy per v2 plan §6
GRAPH_ONLY_KINDS = {
    "dangling_attribute_reference",
    "dangling_relationship_target",
    "entity_source_uri_missing",
    "entity_empty_description",
    "case_no_assertions",
    "case_no_relationships",
    "case_no_documents",
    "document_not_wired_to_case",
    "case_attribute_skill_dangling",
    "marker_nesting_violation",
}

FS_TOUCHING_KINDS = {
    "entity_source_uri_unresolved",
    "agent_skill_not_in_canonical_sandbox",
    "unregistered_document_in_markdown",
    "markdown_section_drift",
}

INFO_KINDS = {"case_marker_absent"}

ALL_KINDS = GRAPH_ONLY_KINDS | FS_TOUCHING_KINDS | INFO_KINDS

SEVERITY = {
    "dangling_attribute_reference": "critical",
    "dangling_relationship_target": "critical",
    "agent_skill_not_in_canonical_sandbox": "critical",
    "case_attribute_skill_dangling": "critical",
    "entity_source_uri_unresolved": "critical",
    "entity_empty_description": "warning",
    "entity_source_uri_missing": "warning",
    "case_no_assertions": "warning",
    "case_no_relationships": "warning",
    "case_no_documents": "warning",
    "document_not_wired_to_case": "warning",
    "unregistered_document_in_markdown": "warning",
    "markdown_section_drift": "warning",
    "marker_nesting_violation": "warning",
    "case_marker_absent": "info",
}


def _finding(
    kind: str, subject: str, detail: str, audit_id: str | None = None
) -> dict[str, Any]:
    """Standard finding shape. audit_id for correlation across runs."""
    severity = SEVERITY.get(kind, "warning")
    if not audit_id:
        audit_id = hashlib.md5(f"{kind}:{subject}".encode()).hexdigest()[:12]
    return {
        "kind": kind,
        "subject": subject,
        "severity": severity,
        "detail": detail,
        "audit_id": audit_id,
    }


def detect_entity_empty_description(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Entities (non-todo/assertion) with no meaningful description."""
    sql = """
        SELECT id, type, name FROM entities
        WHERE (description IS NULL OR trim(description) = '')
        AND type NOT IN ('todo', 'assertion')
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "entity_empty_description",
            r["id"],
            f"{r['type']} '{r['name']}' has empty or missing description",
        )
        for r in rows
    ]


def detect_entity_source_uri_missing(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Entities that should have source_uri but don't (agent_skill, document, case)."""
    sql = """
        SELECT id, type, name FROM entities
        WHERE type IN ('agent_skill', 'document', 'case')
        AND (source_uri IS NULL OR trim(source_uri) = '')
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "entity_source_uri_missing",
            r["id"],
            f"{r['type']} '{r['name']}' missing required source_uri",
        )
        for r in rows
    ]


def detect_dangling_relationship_target(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Relationships pointing to non-existent entities (uses from_entity/to_entity per schema)."""
    sql = """
        SELECT r.id as rel_id, r.from_entity, r.to_entity, r.type
        FROM relationships r
        LEFT JOIN entities e ON e.id = r.to_entity
        WHERE e.id IS NULL AND r.active = 1
    """
    params: tuple = ()
    if subject:
        sql += " AND (r.from_entity = ? OR r.to_entity = ?)"
        params = (subject, subject)
    rows = query(conn, sql, params)
    return [
        _finding(
            "dangling_relationship_target",
            r.get("to_entity") or "unknown",
            f"Relationship {r.get('rel_id')} from {r.get('from_entity')} targets non-existent {r.get('to_entity')}",
        )
        for r in rows
    ]


def detect_case_no_assertions(conn, subject: str | None = None) -> list[dict[str, Any]]:
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


def detect_case_no_documents(conn, subject: str | None = None) -> list[dict[str, Any]]:
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
    import json

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


def detect_marker_nesting_violation(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Nested or malformed CORTEX_GENERATED markers in markdown (fs check)."""
    # This is graph-only in current design (markers recorded as edges/attributes on render).
    # For pre-Phase-4, it's a no-op or simple check. Full fs scan deferred to fs detectors.
    # Placeholder that returns no findings until render integration.
    return []


# --- Filesystem-touching detectors (opt-in) ---


def detect_entity_source_uri_unresolved(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """source_uri that cannot be resolved to an existing file under _FILES_ROOT."""
    sql = "SELECT id, type, source_uri FROM entities WHERE source_uri IS NOT NULL"
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    findings = []
    for r in rows:
        uri = r.get("source_uri")
        if not uri:
            continue
        try:
            path = _FILES_ROOT / uri
            if not path.exists():
                findings.append(
                    _finding(
                        "entity_source_uri_unresolved",
                        r["id"],
                        f"source_uri {uri!r} does not exist on disk",
                    )
                )
        except Exception as e:
            findings.append(
                _finding(
                    "entity_source_uri_unresolved",
                    r["id"],
                    f"Path resolution failed for {uri!r}: {e}",
                )
            )
    return findings


def detect_unregistered_document_in_markdown(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Documents mentioned in markdown but not registered as document: entities.
    Simple stub — full scan would require parsing all markdown files.
    """
    # Full implementation would walk markdown files for entity links and cross-check.
    # For MVP, return empty (expand in Phase 4 with render integration).
    return []


def detect_markdown_section_drift(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Markdown files with CORTEX_GENERATED markers that have drifted from rendered content.
    Requires render comparison — stub for now (full in Phase 4 with render_diff).
    """
    return []


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


# Fs-touching example
def detect_agent_skill_not_in_canonical_sandbox(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """agent_skill: with source_uri not resolving inside agent-skills/ canonical dir."""
    sql = "SELECT id, source_uri FROM entities WHERE type = 'agent_skill'"
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    findings = []
    for r in rows:
        if r["source_uri"]:
            try:
                _validate_canonical_sandbox_path(
                    r["source_uri"], canonical_subdir="agent-skills", must_be_file=True
                )
            except ValueError as e:
                findings.append(
                    _finding(
                        "agent_skill_not_in_canonical_sandbox",
                        r["id"],
                        f"Invalid sandbox path: {str(e)}",
                    )
                )
    return findings


def get_all_detectors() -> dict[str, Any]:
    """Registry of all detectors per v2 plan §6. Graph-only run by default for session_audit."""
    return {
        "dangling_attribute_reference": lambda c, s=None: [],  # TODO: implement (attributes referencing missing entities)
        "dangling_relationship_target": detect_dangling_relationship_target,
        "entity_source_uri_missing": detect_entity_source_uri_missing,
        "entity_empty_description": detect_entity_empty_description,
        "case_no_assertions": detect_case_no_assertions,
        "case_no_relationships": detect_case_no_relationships,
        "case_no_documents": detect_case_no_documents,
        "document_not_wired_to_case": detect_document_not_wired_to_case,
        "case_attribute_skill_dangling": detect_case_attribute_skill_dangling,
        "marker_nesting_violation": detect_marker_nesting_violation,
        "entity_source_uri_unresolved": detect_entity_source_uri_unresolved,
        "agent_skill_not_in_canonical_sandbox": detect_agent_skill_not_in_canonical_sandbox,
        "unregistered_document_in_markdown": detect_unregistered_document_in_markdown,
        "markdown_section_drift": detect_markdown_section_drift,
        "case_marker_absent": detect_case_marker_absent,
    }


def run_detectors(
    kinds: list[str] | None = None,
    subject: str | None = None,
    include_filesystem: bool = False,
) -> list[dict[str, Any]]:
    """Run selected detectors. Graph-only by default (W1). include_filesystem=true adds fs ones."""
    with cortex_conn() as conn:
        detectors = get_all_detectors()
        if kinds is None:
            selected = list(GRAPH_ONLY_KINDS)
            selected.extend(list(INFO_KINDS))
            if include_filesystem:
                selected.extend(list(FS_TOUCHING_KINDS))
        else:
            selected = [k for k in kinds if k in ALL_KINDS]
        findings = []
        for k in selected:
            if k in detectors:
                detector = detectors[k]
                findings.extend(detector(conn, subject))
        return findings


__all__ = ["run_detectors", "ALL_KINDS", "GRAPH_ONLY_KINDS", "SEVERITY"]
