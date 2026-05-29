"""Skill-manifest structural integrity for manifest-bearing entity types.

Companion to migration 041's first-class skill-linkage primitives, extended
to the `todo` type per migration 045 + decision:todo-implementation-skills-binding.
Detects manifest-vs-graph drift on project / plan / plan_phase / todo
entities: an entity carries `required_skills=[...]` in its attributes dict
but its `requires` relationships at the graph layer do not match. Without
the relationship, render_subgraph traversal and AnalyzeImpact-style
reasoning cannot follow the manifest at the graph layer — the attribute
alone is invisible to graph queries.

Two attribute-value shapes are accepted and normalized to the canonical
`agent_skill:<slug>` id before the edge check:

  * Full id `agent_skill:<slug>` (migration 041 shape; project/plan/plan_phase).
  * Bare slug `<slug>` with an optional `#section` anchor (the todo
    convention, e.g. `skill-document-writing#audit-gate-response`); the bare
    slug resolves to `agent_skill:<slug>`.

Drift is flagged in both directions: a manifest entry with no matching
`requires` edge, and (for entities that carry a manifest) a `requires` edge
to an agent_skill absent from the manifest.

Severity: warning. The attribute carries the ground truth; the missing or
extra relationship is a structural enrichment gap, never a critical fault.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...db import query
from ._shared import _finding

# Mirrors the migration 041 spec. Kept in sync via the manifest-shape contract.
_AGENT_SKILL_ID_RE = re.compile(r"^agent_skill:[a-z0-9-]+$")
# Bare agent_skill slug (todo convention, migration 045): pre-`#` segment of a
# required_skills entry that resolves to `agent_skill:<slug>`.
_BARE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

_MANIFEST_TYPES = ("project", "plan", "plan_phase", "todo")


def _normalize_skill_ref(entry: object) -> str | None:
    """Normalize a required_skills entry to a canonical agent_skill id.

    Accepts the full-id form (`agent_skill:<slug>`) and the bare-slug form
    with an optional `#section` anchor (`<slug>` / `<slug>#section`). Returns
    the canonical `agent_skill:<slug>` id, or None when *entry* is not a
    string or its slug does not match the agent_skill grammar.
    """
    if not isinstance(entry, str):
        return None
    ref = entry.split("#", 1)[0].strip()
    if _AGENT_SKILL_ID_RE.match(ref):
        return ref
    if _BARE_SLUG_RE.match(ref):
        return f"agent_skill:{ref}"
    return None


def detect_project_required_skills_no_relationship(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Manifest-bearing entities whose required_skills attribute is not
    mirrored by matching ``requires`` relationships at the graph layer.

    Per migration 041 (extended to `todo` by migration 045): every entry in
    ``attributes.required_skills`` SHOULD also appear as a ``requires``
    relationship from the entity to the corresponding ``agent_skill:`` entity.
    Mismatch in either direction flags as
    ``project_required_skills_no_relationship``.

    Malformed entries (non-string members, or values that do not resolve to
    the agent_skill grammar) are flagged inline — they would never be findable
    as relationships, so the structural gap is the auditor-visible surface.
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

    # Pass 1 — collect normalized manifest entries per entity, surface malformed.
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
                    "agent_skill references.",
                )
            )
            continue

        valid_ids: list[str] = []
        for entry in manifest:
            normalized = _normalize_skill_ref(entry)
            if normalized is None:
                findings.append(
                    _finding(
                        "project_required_skills_no_relationship",
                        r["id"],
                        f"{r['type']} '{r['name']}' required_skills "
                        f"entry {entry!r} does not resolve to an "
                        "agent_skill id (expected `agent_skill:<slug>` or "
                        "a bare `<slug>` / `<slug>#section`, slug matching "
                        "`^[a-z0-9-]+$`).",
                    )
                )
                continue
            if normalized not in valid_ids:
                valid_ids.append(normalized)
        if valid_ids:
            row_skills.append((r, valid_ids))

    if not row_skills:
        return findings

    # Pass 2 — bulk-load existing `requires` relationships from the entities
    # being checked, then diff against the normalized manifest. One query.
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
        manifest_set = set(manifest)
        # Forward drift — manifest entry with no matching requires edge.
        for skill_id in manifest:
            if skill_id not in wired:
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
        # Inverse drift — requires edge to an agent_skill absent from manifest.
        for skill_id in sorted(wired):
            if skill_id.startswith("agent_skill:") and skill_id not in manifest_set:
                findings.append(
                    _finding(
                        "project_required_skills_no_relationship",
                        r["id"],
                        f"{r['type']} '{r['name']}' has an active `requires` "
                        f"relationship from {r['id']} → {skill_id} but "
                        f"{skill_id} is absent from its required_skills "
                        "attribute — attribute⟷edge drift. Add the skill to "
                        "required_skills or retire the relationship.",
                    )
                )

    return findings


__all__ = ["detect_project_required_skills_no_relationship"]
