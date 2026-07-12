"""Derived-view staleness detectors — Tier-0 playbook_stale and core-hash mismatch."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ...db import query
from .._views import (
    check_attr_edge_parity,
    compute_core_hash,
    extract_core_sections,
    load_recipe,
    parse_recipe_id,
    snapshot_for_scope,
)
from ._shared import _finding

_KIND_STALE = "playbook_stale"
_KIND_HASH = "view_core_hash_mismatch"


def _decode_attrs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _registered_views(conn, subject: str | None) -> list[dict[str, Any]]:
    sql = """
        SELECT id, attributes, source_uri
        FROM entities
        WHERE type = 'document'
          AND attributes LIKE '%derived_from_snapshot%'
    """
    params: list[Any] = []
    if subject:
        sql += " AND (id = ? OR id IN (SELECT from_entity FROM relationships WHERE to_entity = ? AND type = 'derived_from' AND active = 1))"
        params.extend([subject, subject])
    rows = query(conn, sql, tuple(params))
    return [dict(r) for r in rows]


def _root_for_view(conn, document_id: str) -> str | None:
    rows = query(
        conn,
        """
        SELECT to_entity FROM relationships
        WHERE from_entity = ? AND type = 'derived_from' AND active = 1
        LIMIT 1
        """,
        (document_id,),
    )
    return rows[0]["to_entity"] if rows else None


def _stable_audit_id(kind: str, document_id: str, section_id: str) -> str:
    return hashlib.md5(f"{kind}:{document_id}:{section_id}".encode()).hexdigest()[:12]


def detect_playbook_stale(conn, subject: str | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in _registered_views(conn, subject):
        document_id = row["id"]
        attrs = _decode_attrs(row.get("attributes"))
        profile = attrs.get("view_profile")
        recipe_id = attrs.get("derivation_recipe")
        if not profile or not recipe_id:
            continue
        try:
            prof, version = parse_recipe_id(recipe_id)
            recipe = load_recipe(prof, version)
        except Exception:
            continue
        root_id = _root_for_view(conn, document_id)
        prior_snapshot = attrs.get("derived_from_snapshot") or {}
        current_snapshot = snapshot_for_scope(conn, recipe, root_id)
        if not check_attr_edge_parity(
            conn, document_id=document_id, root_id=root_id, profile=profile
        ):
            detail = json.dumps(
                {
                    "view_span": "registration",
                    "verdict": "attr_edge_parity_break",
                    "source_ids": [root_id] if root_id else [],
                }
            )
            findings.append(
                _finding(
                    _KIND_STALE,
                    document_id,
                    detail,
                    audit_id=_stable_audit_id(_KIND_STALE, document_id, "registration"),
                )
            )
        if current_snapshot.get("max_assertion_id", 0) > prior_snapshot.get(
            "max_assertion_id", 0
        ):
            for section in recipe.get("sections", []):
                section_id = section["section_id"]
                detail = json.dumps(
                    {
                        "view_span": section_id,
                        "verdict": "watched_set_high_water",
                        "source_ids": [],
                    }
                )
                findings.append(
                    _finding(
                        _KIND_STALE,
                        document_id,
                        detail,
                        audit_id=_stable_audit_id(_KIND_STALE, document_id, section_id),
                    )
                )
        section_stamps = attrs.get("section_stamps") or {}
        for section_id, stamp in section_stamps.items():
            cited = stamp.get("cited_ids") if isinstance(stamp, dict) else None
            if not cited:
                continue
            for cited_id in cited:
                if cited_id.startswith("assertion:"):
                    aid = int(cited_id.split(":", 1)[1])
                    rows = query(
                        conn,
                        "SELECT superseded_by FROM assertions WHERE id = ?",
                        (aid,),
                    )
                    if not rows or rows[0]["superseded_by"]:
                        detail = json.dumps(
                            {
                                "view_span": section_id,
                                "verdict": "dead_citation",
                                "source_ids": [cited_id],
                            }
                        )
                        findings.append(
                            _finding(
                                _KIND_STALE,
                                document_id,
                                detail,
                                audit_id=_stable_audit_id(
                                    _KIND_STALE, document_id, f"{section_id}:{cited_id}"
                                ),
                            )
                        )
    return findings


def detect_view_core_hash_mismatch(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    from .._shared import _FILES_ROOT

    findings: list[dict[str, Any]] = []
    for row in _registered_views(conn, subject):
        document_id = row["id"]
        attrs = _decode_attrs(row.get("attributes"))
        stamped_hash = attrs.get("core_hash")
        source_uri = row.get("source_uri")
        if not stamped_hash or not source_uri:
            continue
        rel = source_uri.removeprefix("cortex://")
        path = _FILES_ROOT / rel
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8")
        core = extract_core_sections(body)
        if not core:
            continue
        actual = compute_core_hash(core)
        if actual != stamped_hash:
            for section_id in sorted(core):
                detail = json.dumps(
                    {
                        "view_span": section_id,
                        "verdict": "core_hash_mismatch",
                        "source_ids": [],
                    }
                )
                findings.append(
                    _finding(
                        _KIND_HASH,
                        document_id,
                        detail,
                        audit_id=_stable_audit_id(_KIND_HASH, document_id, section_id),
                    )
                )
    return findings


__all__ = ["detect_playbook_stale", "detect_view_core_hash_mismatch"]
