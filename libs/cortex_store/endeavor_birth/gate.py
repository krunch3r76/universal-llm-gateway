"""Birth gate WARNING on entity create/update (F-B1 / AC4)."""

from __future__ import annotations

import sqlite3
from typing import Any

from .constants import ACK_ATTR, LEGACY_THREAD_KEYS
from .events import cortex_endeavor_birth_incomplete
from .read_models import birth_missing_pointers


def check_endeavor_birth_incomplete(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    attrs: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return advisory warning payload when endeavor birth pointers are missing."""
    merged = dict(attrs or {})
    for legacy in LEGACY_THREAD_KEYS:
        if legacy in merged:
            return {
                "kind": "endeavor_birth_incomplete",
                "host": entity_id,
                "detail": f"legacy thread key {legacy!r} is forbidden; use ring_thread",
                "missing": ["ring_thread"],
                "resume_blocking": True,
            }
    missing = birth_missing_pointers(
        conn,
        entity_type=entity_type,
        attrs=merged,
        host_id=entity_id,
    )
    if not missing:
        return None
    ack = merged.get(ACK_ATTR)
    if ack:
        return None
    warning = {
        "kind": "endeavor_birth_incomplete",
        "host": entity_id,
        "missing": missing,
        "resume_blocking": any(k in {"ring_thread", "endeavor_charter_uri"} for k in missing),
        "ack": False,
    }
    cortex_endeavor_birth_incomplete(
        host=entity_id,
        missing=missing,
        resume_blocking=warning["resume_blocking"],
        ack=False,
    )
    return warning


def attach_endeavor_birth_warning(result: dict[str, Any], warning: dict[str, Any]) -> None:
    result["endeavor_birth_warning"] = warning
    hint = (
        "endeavor_birth_incomplete (advisory): missing "
        f"{warning.get('missing', [])}"
    )
    if "_next" in result:
        result["_next"] = f"{result['_next']}; {hint}"
    else:
        result["_next"] = hint
