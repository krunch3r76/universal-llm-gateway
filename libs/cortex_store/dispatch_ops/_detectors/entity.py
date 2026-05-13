"""Entity-level detectors: empty description, missing source_uri, fs-unresolved
source_uri, non-canonical agent-skill sandbox.
"""

from __future__ import annotations

from typing import Any

from ...db import query
from .._shared import _FILES_ROOT, _validate_canonical_sandbox_path
from ._shared import _finding


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


__all__ = [
    "detect_agent_skill_not_in_canonical_sandbox",
    "detect_entity_empty_description",
    "detect_entity_source_uri_missing",
    "detect_entity_source_uri_unresolved",
]
