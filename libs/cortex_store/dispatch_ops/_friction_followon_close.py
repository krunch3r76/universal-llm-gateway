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
    raw_fid = attrs.get("spawned_by_friction")
    if raw_fid is None:
        return None
    try:
        friction_id = int(raw_fid)
    except (TypeError, ValueError):
        logger.warning(
            "todo %s has non-int spawned_by_friction=%r — skip auto-close",
            entity_id,
            raw_fid,
        )
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
