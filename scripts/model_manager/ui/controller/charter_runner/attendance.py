"""Per-root attendance resolution from durable todo attributes."""

from __future__ import annotations

from typing import Literal

from universal_logging import get_logger

from . import bus_client
from .dispatch_client import AdmissionMode

logger = get_logger(__name__)

Attendance = Literal["attended", "autonomous", "operator_proxy"]


def attendance_from_todo_attrs(attrs: dict | None) -> Attendance:
    """Map todo ``attendance`` attr; default attended."""
    if not isinstance(attrs, dict):
        return "attended"
    raw = str(attrs.get("attendance") or "").strip().lower()
    if raw == "autonomous":
        return "autonomous"
    if raw == "operator_proxy":
        return "operator_proxy"
    return "attended"


def admission_mode_for_attendance(attendance: Attendance) -> AdmissionMode:
    """Kernel/tick bind: autonomous → autonomous; operator_proxy → operator_proxy; attended → generate."""
    if attendance == "autonomous":
        return "autonomous"
    if attendance == "operator_proxy":
        return "operator_proxy"
    return "generate"


async def _charter_todo_for_root(root_id: str) -> str | None:
    """Resolve charter todo slug from root thread metadata or latest CHECKPOINT."""
    rid = root_id.removeprefix("agent-bus:")
    try:
        turns = await bus_client.fetch_turns(rid)
    except Exception as exc:
        logger.warning("charter todo lookup failed root_id=%s: %s", root_id, exc)
        return None
    for turn in reversed(turns):
        subj = str(turn.get("subject") or "")
        if not subj.upper().startswith("CHECKPOINT"):
            continue
        body = str(turn.get("body") or "")
        from .checkpoint_schema import parse_checkpoint

        try:
            parsed = parse_checkpoint(body)
        except Exception:
            continue
        if parsed.source_ref:
            return parsed.source_ref.lower()
    return None


async def _attendance_from_bus_tags(root_id: str) -> Attendance | None:
    """Honor ``attendance:autonomous`` / ``attendance:operator_proxy`` on the enrolled bus thread."""
    rid = root_id.removeprefix("agent-bus:")
    try:
        thread = await bus_client.fetch_thread(rid)
    except Exception as exc:
        logger.warning("bus tag attendance lookup failed root_id=%s: %s", root_id, exc)
        return None
    if not thread:
        return None
    tags = [str(t).strip().lower() for t in (thread.get("tags") or [])]
    if "attendance:autonomous" in tags:
        return "autonomous"
    if "attendance:operator_proxy" in tags:
        return "operator_proxy"
    return None


async def resolve_attendance(root_id: str) -> Attendance:
    """Resolve attendance once per tick: todo attrs → bus thread tag → attended."""
    todo_ref = await _charter_todo_for_root(root_id)
    if todo_ref:
        try:
            from cortex_store.dispatch_ops.ops_entities import _op_entity_get

            ent = _op_entity_get(entity_id=todo_ref, intent="full")
        except Exception as exc:
            logger.warning(
                "cortex todo attrs lookup failed root_id=%s todo=%s: %s",
                root_id,
                todo_ref,
                exc,
            )
            ent = {}
        if "error" not in ent:
            attrs = ent.get("attributes")
            mode = attendance_from_todo_attrs(
                attrs if isinstance(attrs, dict) else None
            )
            if mode != "attended":
                return mode
    tagged = await _attendance_from_bus_tags(root_id)
    if tagged is not None:
        return tagged
    return "attended"


__all__ = [
    "Attendance",
    "admission_mode_for_attendance",
    "attendance_from_todo_attrs",
    "resolve_attendance",
]
