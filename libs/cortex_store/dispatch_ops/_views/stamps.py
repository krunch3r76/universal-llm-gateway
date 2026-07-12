"""PROV stamps and view registration attribute helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from ...db import query

_STAMP_RE = re.compile(r"<!-- view-stamp: (?P<json>\{.*\}) -->")


def build_stamp(
    *,
    document_id: str,
    view_profile: str,
    derivation_recipe: str,
    view_rev: int,
    core_hash: str,
    content_hash: str,
    derived_from_snapshot: dict[str, Any],
    section_stamps: dict[str, Any],
    prior_revision_uri: str | None,
    mode: str,
    agent: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    return {
        "used": [
            derived_from_snapshot,
            {"section_stamps": section_stamps},
            *([prior_revision_uri] if prior_revision_uri else []),
        ],
        "activity": {
            "op": "view_render",
            "recipe": derivation_recipe,
            "mode": mode,
            "agent": agent,
            "session_id": session_id,
        },
        "generated": {
            "document_id": document_id,
            "view_rev": view_rev,
            "core_hash": core_hash,
            "content_hash": content_hash,
            "view_profile": view_profile,
        },
        "time": derived_from_snapshot.get("as_of"),
    }


def parse_stamp_from_body(body: str) -> dict[str, Any] | None:
    for line in body.splitlines():
        match = _STAMP_RE.match(line.strip())
        if match:
            try:
                parsed = json.loads(match.group("json"))
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def view_registration_attrs(
    *,
    view_profile: str,
    derivation_recipe: str,
    derived_from_snapshot: dict[str, Any],
    view_rev: int,
    core_hash: str,
    section_stamps: dict[str, Any],
    prior_revision_uri: str | None = None,
) -> dict[str, Any]:
    attrs = {
        "view_profile": view_profile,
        "derivation_recipe": derivation_recipe,
        "derived_from_snapshot": derived_from_snapshot,
        "view_rev": view_rev,
        "core_hash": core_hash,
        "section_stamps": section_stamps,
    }
    if prior_revision_uri:
        attrs["prior_revision_uri"] = prior_revision_uri
    return attrs


def check_attr_edge_parity(
    conn,
    *,
    document_id: str,
    root_id: str | None,
    profile: str,
) -> bool:
    if profile == "matter_index" and not root_id:
        return True
    if not root_id:
        return False
    rows = query(
        conn,
        """
        SELECT 1 FROM relationships
        WHERE from_entity = ?
          AND to_entity = ?
          AND type = 'derived_from'
          AND active = 1
        LIMIT 1
        """,
        (document_id, root_id),
    )
    return bool(rows)


__all__ = [
    "build_stamp",
    "check_attr_edge_parity",
    "parse_stamp_from_body",
    "view_registration_attrs",
]
