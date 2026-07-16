"""5129 repair recipe + idempotent rerun (F-B5 / A2)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import query
from .constants import LEGACY_THREAD_KEYS
from .events import cortex_endeavor_repaired
from .read_models import _attrs

_T1_HOST = "opportunity:scc-pharmacist-outpatient-26R27D"
_CHARTER_URI = "cortex://notes/system/threads/5129-endeavor-charter.md"
_SCOREBOARD_URI = "cortex://notes/system/threads/5129-endeavor-scoreboard.md"


def _needs_repair(attrs: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if attrs.get("bus_thread") and not attrs.get("ring_thread"):
        gaps.append("ring_thread_rename")
    if not attrs.get("endeavor_charter_uri"):
        gaps.append("endeavor_charter_uri")
    if not attrs.get("endeavor_scoreboard_uri"):
        gaps.append("endeavor_scoreboard_uri")
    if not attrs.get("ring_thread") and attrs.get("bus_thread"):
        gaps.append("ring_thread")
    return gaps


def apply_5129_repair(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = query(
        conn,
        "SELECT id, attributes FROM entities WHERE id = ?",
        (_T1_HOST,),
    )
    if not rows:
        return {"tier": "T1", "repaired": 0, "residual": 1, "applied": False}
    row = rows[0]
    attrs = _attrs(row)
    gaps = _needs_repair(attrs)
    if not gaps:
        result = {"tier": "T1", "repaired": 0, "residual": 0, "applied": False}
        cortex_endeavor_repaired(**result)
        return result
    new_attrs = dict(attrs)
    ring = new_attrs.get("ring_thread") or new_attrs.get("bus_thread") or "5129"
    new_attrs["ring_thread"] = str(ring)
    new_attrs["endeavor_charter_uri"] = _CHARTER_URI
    new_attrs["endeavor_scoreboard_uri"] = _SCOREBOARD_URI
    for legacy in LEGACY_THREAD_KEYS:
        new_attrs.pop(legacy, None)
    conn.execute(
        "UPDATE entities SET attributes = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(new_attrs), _T1_HOST),
    )
    residual = len(_needs_repair(new_attrs))
    result = {
        "tier": "T1",
        "repaired": len(gaps),
        "residual": residual,
        "applied": residual == 0,
    }
    cortex_endeavor_repaired(**result)
    return result
