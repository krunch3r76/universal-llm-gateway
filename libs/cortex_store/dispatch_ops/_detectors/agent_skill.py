"""Skill-companion structural integrity for ``agent_skill`` entities.

Mirrors the ``required_skills``/``requires`` contract in ``project.py`` for
skill→skill companions: an ``agent_skill`` carries ``related_skills=[...]`` in
its attributes dict but its ``references`` / ``related_to`` relationships at
the graph layer do not match. The attribute is ground truth; relationships
mirror it for graph traversal.

Bare slugs in ``related_skills`` resolve to ``agent_skill:<slug>``. Edge
satisfaction accepts either a directional ``references`` row
(source→target) or a canonical ``related_to`` row (sorted endpoints).

Severity: warning. Separate from ``project_required_skills_no_relationship``
— does not reuse ``requires`` or ``project.py``'s ``_MANIFEST_TYPES``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...db import query
from ...guidance_entity import GUIDANCE_ID_PREFIXES, entity_slug_from_id
from ._shared import _finding

_INGEST_RELATED_REMEDIATION = (
    "Update the declared related_skills list in the SKILL.md / cortex SOT, then run: "
    "python scripts/cortex/ingest_skills.py"
)

_BARE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_SKILL_EDGE_TYPES = ("references", "related_to")


def _is_guidance_id(entity_id: str) -> bool:
    return any(entity_id.startswith(prefix) for prefix in GUIDANCE_ID_PREFIXES)


def _normalize_related_slug(entry: object) -> str | None:
    """Bare slug for a related_skills entry (prefix-agnostic).

    arc 3924: related_skills targets may be typed under any guidance prefix
    (agent_skill:/rule:/skill:) after the corpus migration; edge matching is done
    on the bare slug so a retyped companion is not reported as missing.
    """
    if not isinstance(entry, str):
        return None
    slug = entity_slug_from_id(entry.split("#", 1)[0].strip())
    if not _BARE_SLUG_RE.match(slug):
        return None
    return slug


def _edge_key(from_entity: str, to_entity: str, rel_type: str) -> tuple[str, str, str]:
    if rel_type == "related_to":
        lo, hi = sorted((from_entity, to_entity))
        return lo, hi, rel_type
    return from_entity, to_entity, rel_type


def _edge_covers(
    source_id: str, target_id: str, wired: set[tuple[str, str, str]]
) -> bool:
    if _edge_key(source_id, target_id, "references") in wired:
        return True
    return _edge_key(source_id, target_id, "related_to") in wired


def detect_agent_skill_related_skills_no_relationship(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """``agent_skill`` entities whose ``related_skills`` attribute is not
    mirrored by matching ``references`` or ``related_to`` relationships."""
    sql = (
        "SELECT id, type, name, attributes FROM entities "
        "WHERE type IN ('agent_skill', 'rule', 'skill') AND attributes IS NOT NULL"
    )
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)

    rows = query(conn, sql, params)

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
        manifest = attrs.get("related_skills")
        if not manifest:
            continue
        if not isinstance(manifest, list):
            findings.append(
                _finding(
                    "agent_skill_related_skills_no_relationship",
                    r["id"],
                    f"{r['type']} '{r['name']}' has related_skills attribute of "
                    f"non-list type {type(manifest).__name__} — expected list of "
                    "bare skill slugs.",
                )
            )
            continue

        valid_ids: list[str] = []
        for entry in manifest:
            normalized = _normalize_related_slug(entry)
            if normalized is None:
                findings.append(
                    _finding(
                        "agent_skill_related_skills_no_relationship",
                        r["id"],
                        f"{r['type']} '{r['name']}' related_skills entry "
                        f"{entry!r} does not resolve to a guidance skill slug "
                        "(expected bare `<slug>` matching `^[a-z0-9-]+$`).",
                    )
                )
                continue
            if normalized not in valid_ids:
                valid_ids.append(normalized)
        if valid_ids:
            row_skills.append((r, valid_ids))

    if not row_skills:
        return findings

    entity_ids = [r["id"] for r, _ in row_skills]
    placeholders = ",".join("?" for _ in entity_ids)
    # related_to rows are stored with canonical min/max endpoints — either
    # endpoint may appear as from_entity or to_entity.
    rel_rows = query(
        conn,
        f"SELECT from_entity, to_entity, type FROM relationships "
        f"WHERE active = 1 AND ("
        f"  (type = 'references' AND from_entity IN ({placeholders})) "
        f"  OR (type = 'related_to' AND (from_entity IN ({placeholders}) "
        f"      OR to_entity IN ({placeholders})))"
        f")",
        (*entity_ids, *entity_ids, *entity_ids),
    )
    # arc 3924: match on bare slugs. entity_retype rewrites relationship
    # endpoints to the migrated prefix, so a companion retyped agent_skill→rule
    # must still match its related_skills bare slug. The reverse-drift check
    # only considers guidance-typed targets (skill companions).
    wired: set[tuple[str, str, str]] = set()
    wired_by_source: dict[str, set[str]] = {}
    for rel in rel_rows:
        from_slug = entity_slug_from_id(rel["from_entity"])
        to_slug = entity_slug_from_id(rel["to_entity"])
        wired.add(_edge_key(from_slug, to_slug, rel["type"]))
        if rel["type"] == "references":
            if _is_guidance_id(rel["to_entity"]):
                wired_by_source.setdefault(from_slug, set()).add(to_slug)
        elif rel["type"] == "related_to":
            if _is_guidance_id(rel["to_entity"]):
                wired_by_source.setdefault(from_slug, set()).add(to_slug)
            if _is_guidance_id(rel["from_entity"]):
                wired_by_source.setdefault(to_slug, set()).add(from_slug)

    for r, manifest in row_skills:
        source_id = r["id"]
        source_slug = entity_slug_from_id(source_id)
        manifest_set = set(manifest)
        for skill_slug in manifest:
            if not _edge_covers(source_slug, skill_slug, wired):
                findings.append(
                    _finding(
                        "agent_skill_related_skills_no_relationship",
                        source_id,
                        f"{r['type']} '{r['name']}' related_skills lists "
                        f"{skill_slug} but no active `references` or `related_to` "
                        f"relationship from {source_id} → {skill_slug} exists. "
                        f"Wire via {_INGEST_RELATED_REMEDIATION}",
                    )
                )
        wired_targets = wired_by_source.get(source_slug, set())
        for skill_slug in sorted(wired_targets):
            if skill_slug not in manifest_set:
                findings.append(
                    _finding(
                        "agent_skill_related_skills_no_relationship",
                        source_id,
                        f"{r['type']} '{r['name']}' has an active skill-link "
                        f"relationship to {skill_slug} but it is absent from its "
                        "related_skills attribute — attribute⟷edge drift. "
                        f"{_INGEST_RELATED_REMEDIATION}",
                    )
                )

    return findings


__all__ = ["detect_agent_skill_related_skills_no_relationship"]
