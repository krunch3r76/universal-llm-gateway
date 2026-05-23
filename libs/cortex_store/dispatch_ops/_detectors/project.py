"""Project / plan / plan_phase detectors — skill-manifest structural integrity.

Companion to migration 041's first-class skill-linkage primitives. Detects
manifest-vs-graph drift: an entity carries `required_skills=[...]` in its
attributes dict but does NOT have `requires` relationships pointing at the
named skill entities. Without the relationship, render_subgraph traversal
and AnalyzeImpact-style reasoning cannot follow the manifest at the graph
layer — the attribute alone is invisible to graph queries.

Severity: warning. The attribute carries the ground truth; the missing
relationship is a structural enrichment gap, never a critical fault.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...db import query
from ._shared import _finding

# Mirrors the migration 041 spec. Kept in sync via the manifest-shape contract.
_AGENT_SKILL_ID_RE = re.compile(r"^agent_skill:[a-z0-9-]+$")

_MANIFEST_TYPES = ("project", "plan", "plan_phase")


def detect_project_required_skills_no_relationship(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Project / plan / plan_phase entities whose required_skills attribute
    is not mirrored by ``requires`` relationships at the graph layer.

    Per migration 041: every string in ``attributes.required_skills``
    SHOULD also appear as a ``requires`` relationship from the entity to
    the corresponding ``agent_skill:`` entity. Absence flags as
    ``project_required_skills_no_relationship``.

    Also flags malformed entries inline (non-string members, IDs that
    don't match the agent_skill pattern) — these would never be findable
    as relationships, so the structural gap is the auditor-visible
    surface.
    """
    placeholders = ",".join("?" for _ in _MANIFEST_TYPES)
    sql = (
        "SELECT id, type, name, attributes FROM entities "
        f"WHERE type IN ({placeholders}) AND attributes IS NOT NULL"
    )
    params: tuple = tuple(_MANIFEST_TYPES)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)

    rows = query(conn, sql, params)

    # Pass 1 — collect manifest entries per entity, surface malformed entries.
    findings: list[dict[str, Any]] = []
    row_skills: list[tuple[dict, list[str]]] = []
    for r in rows:
        attrs = r.get("attributes")
        try:
            if isinstance(attrs, str):
                attrs = json.loads(attrs)
        except json.JSONDecodeError as exc:
            findings.append(
                _finding(
                    "dangling_attribute_reference",
                    r["id"],
                    f"Malformed attributes JSON — could not parse: {exc}",
                )
            )
            continue
        if not isinstance(attrs, dict):
            continue
        manifest = attrs.get("required_skills")
        if not manifest:
            continue
        if not isinstance(manifest, list):
            findings.append(
                _finding(
                    "project_required_skills_no_relationship",
                    r["id"],
                    f"{r['type']} '{r['name']}' has required_skills "
                    f"attribute of non-list type "
                    f"{type(manifest).__name__} — expected list of "
                    "agent_skill IDs.",
                )
            )
            continue

        valid_ids: list[str] = []
        for entry in manifest:
            if not isinstance(entry, str) or not _AGENT_SKILL_ID_RE.match(entry):
                findings.append(
                    _finding(
                        "project_required_skills_no_relationship",
                        r["id"],
                        f"{r['type']} '{r['name']}' required_skills "
                        f"entry {entry!r} does not match "
                        "agent_skill ID pattern "
                        "`^agent_skill:[a-z0-9-]+$`.",
                    )
                )
                continue
            valid_ids.append(entry)
        if valid_ids:
            row_skills.append((r, valid_ids))

    if not row_skills:
        return findings

    # Pass 2 — bulk-load existing `requires` relationships from the entities
    # being checked, then diff against the manifest. One query for all rows.
    entity_ids = [r["id"] for r, _ in row_skills]
    placeholders = ",".join("?" for _ in entity_ids)
    rel_rows = query(
        conn,
        f"SELECT from_entity, to_entity FROM relationships "
        f"WHERE type = 'requires' AND active = 1 "
        f"AND from_entity IN ({placeholders})",
        tuple(entity_ids),
    )
    existing: dict[str, set[str]] = {}
    for rel in rel_rows:
        existing.setdefault(rel["from_entity"], set()).add(rel["to_entity"])

    for r, manifest in row_skills:
        wired = existing.get(r["id"], set())
        missing = [skill_id for skill_id in manifest if skill_id not in wired]
        for skill_id in missing:
            findings.append(
                _finding(
                    "project_required_skills_no_relationship",
                    r["id"],
                    f"{r['type']} '{r['name']}' required_skills lists "
                    f"{skill_id} but no active `requires` relationship "
                    f"from {r['id']} → {skill_id} exists. Wire via "
                    "relationship_create or rerun migration 041's "
                    "depends_on→requires backfill.",
                )
            )

    return findings


__all__ = ["detect_project_required_skills_no_relationship"]
