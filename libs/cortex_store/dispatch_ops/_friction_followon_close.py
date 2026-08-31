"""Close parent friction when a spawned follow-on todo reaches done.

Minting a follow-on todo is not friction_close — without this hook, ship
evidence on the todo leaves the parent assertion open (dogfood 5854).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from universal_logging import get_logger

from ..db import query
from ._shared import record

logger = get_logger("cortex-api.dispatch_ops.friction_followon_close")


def _parse_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _parse_friction_id(raw: Any) -> int | None:
    """Accept int, digit string, or ``a:31467`` / ``A:31467``."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    text = str(raw).strip()
    if len(text) >= 2 and text[1] == ":" and text[0] in {"a", "A"}:
        text = text[2:].strip()
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _friction_id_from_attrs(attrs: dict[str, Any]) -> int | None:
    """Canonical close key is ``spawned_by_friction``; seed alias is accepted.

    ``/work-item-seed`` stamped ``derived_from_friction: "a:31467"`` while the
    hook only read integer ``spawned_by_friction`` — todo-done then left the
    parent open (a:31467).
    """
    for key in ("spawned_by_friction", "derived_from_friction"):
        if key not in attrs:
            continue
        parsed = _parse_friction_id(attrs.get(key))
        if parsed is not None:
            return parsed
        logger.warning(
            "todo attr %s=%r is not a friction id — skip that key",
            key,
            attrs.get(key),
        )
    return None


def close_spawned_friction_on_todo_done(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    new_workflow_state: str,
    prior_workflow_state: str | None,
) -> dict[str, Any] | None:
    """``friction_close`` the parent when a spawned follow-on todo hits done.

    Idempotent: already-closed parents return ``already_closed``. Runs only on
    the open→done transition. Returns the close result or ``None`` when N/A.
    """
    if entity_type != "todo":
        return None
    if new_workflow_state != "done":
        return None
    if prior_workflow_state == "done":
        return None

    rows = query(
        conn,
        "SELECT attributes FROM entities WHERE id = ?",
        (entity_id,),
    )
    if not rows:
        return None
    attrs = _parse_attributes(rows[0]["attributes"])
    friction_id = _friction_id_from_attrs(attrs)
    if friction_id is None:
        return None

    from ._friction_close_impl import close_friction_assertion

    result = close_friction_assertion(
        friction_id,
        f"todo:{entity_id}",
        agent="cortex-api",
        session_id="friction-followon-todo-done",
        resolution_note=(
            f"auto-closed: follow-on {entity_id} transitioned to workflow_state=done"
        ),
    )
    if "error" in result:
        logger.error(
            "auto friction_close failed for a:%s from todo %s: %s",
            friction_id,
            entity_id,
            result["error"],
        )
        record(
            "cortex.friction.followon_close_failed",
            assertion_id=friction_id,
            todo_id=entity_id,
            error=str(result["error"]),
        )
        return result

    status = result.get("status") or "closed"
    record(
        "cortex.friction.followon_closed",
        assertion_id=friction_id,
        todo_id=entity_id,
        status=status,
        fulfillment_assertion_id=result.get("fulfillment_assertion_id"),
    )
    logger.info(
        "auto friction_close a:%s via todo %s status=%s",
        friction_id,
        entity_id,
        status,
    )
    return result


__all__ = ["close_spawned_friction_on_todo_done"]
