"""Shared read models for birth gate and T1 audit (F-M5 / A3)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import query
from ..type_taxonomy import MATTER_SPECIES
from .constants import SCOREBOARD_KEY, STAGE_S2


def _attrs(entity_row: dict[str, Any]) -> dict[str, Any]:
    raw = entity_row.get("attributes")
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def endeavor_host(attrs: dict[str, Any]) -> bool:
    return attrs.get("mode") == "endeavor"


def stage(attrs: dict[str, Any]) -> str | None:
    value = attrs.get("stage")
    return str(value) if value is not None else None


def deliverables(conn: sqlite3.Connection, host_id: str) -> set[str]:
    rows = query(
        conn,
        "SELECT to_entity FROM relationships "
        "WHERE active = 1 AND from_entity = ? AND type = 'deliverable'",
        (host_id,),
    )
    return {str(r["to_entity"]) for r in rows}


def scoreboard_required(
    conn: sqlite3.Connection,
    host_id: str,
    attrs: dict[str, Any],
) -> bool:
    if not endeavor_host(attrs):
        return False
    st = stage(attrs)
    if st == STAGE_S2:
        return True
    return len(deliverables(conn, host_id)) > 1


def birth_missing_pointers(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    attrs: dict[str, Any],
    host_id: str | None = None,
) -> list[str]:
    if entity_type not in MATTER_SPECIES or not endeavor_host(attrs):
        return []
    missing: list[str] = []
    for key in ("endeavor_charter_uri", "ring_thread"):
        if not attrs.get(key):
            missing.append(key)
    if host_id and scoreboard_required(conn, host_id, attrs):
        if not attrs.get(SCOREBOARD_KEY):
            missing.append(SCOREBOARD_KEY)
    return missing


def host_entity_row(conn: sqlite3.Connection, host_id: str) -> dict[str, Any] | None:
    rows = query(conn, "SELECT id, type, attributes FROM entities WHERE id = ?", (host_id,))
    return rows[0] if rows else None
