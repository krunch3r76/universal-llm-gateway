"""Per-root attendance resolution from durable todo attributes."""

from __future__ import annotations

from typing import Literal

from .dispatch_client import AdmissionMode

Attendance = Literal["attended", "autonomous"]


def attendance_from_todo_attrs(attrs: dict | None) -> Attendance:
    """Map todo ``attendance`` attr; default attended."""
    if not isinstance(attrs, dict):
        return "attended"
    raw = str(attrs.get("attendance") or "").strip().lower()
    if raw == "autonomous":
        return "autonomous"
    return "attended"


def admission_mode_for_attendance(attendance: Attendance) -> AdmissionMode:
    """Kernel/tick bind: autonomous → autonomous packet; attended → generate."""
    if attendance == "autonomous":
        return "autonomous"
    return "generate"


def _attendance_from_bus_tags(root_id: str) -> Attendance | None:
    """Honor ``attendance:autonomous`` on the enrolled bus thread when set."""
    rid = root_id.removeprefix("agent-bus:")
    try:
        from agent_bus_store.db.threads import get_thread

        thread = get_thread(rid)
    except Exception:  # noqa: BLE001
        return None
    if not thread:
        return None
    tags = [str(t).strip().lower() for t in (thread.get("tags") or [])]
    if "attendance:autonomous" in tags:
        return "autonomous"
    return None


def default_attendance_lookup(root_id: str) -> Attendance:
    """Read attendance from todo attrs, else enrolled bus thread tag."""
    todo_ref = _charter_todo_for_root(root_id)
    if todo_ref:
        try:
            from cortex_store.dispatch_ops.ops_entities import _op_entity_get

            ent = _op_entity_get(entity_id=todo_ref, intent="full")
        except Exception:  # noqa: BLE001 — offline / missing cortex
            ent = {}
        if "error" not in ent:
            attrs = ent.get("attributes")
            mode = attendance_from_todo_attrs(
                attrs if isinstance(attrs, dict) else None
            )
            if mode == "autonomous":
                return "autonomous"
    tagged = _attendance_from_bus_tags(root_id)
    if tagged is not None:
        return tagged
    return "attended"


def admission_mode_for_root(root_id: str) -> AdmissionMode:
    """Per-root admission mode — replaces global ``CHARTER_ADMISSION_MODE`` arming."""
    return admission_mode_for_attendance(default_attendance_lookup(root_id))


def _charter_todo_for_root(root_id: str) -> str | None:
    """Resolve charter todo slug from root thread metadata or latest CHECKPOINT."""
    rid = root_id.removeprefix("agent-bus:")
    try:
        from agent_bus_store.db import get_thread_turns_asc

        turns = get_thread_turns_asc(rid)
    except Exception:  # noqa: BLE001
        turns = []
    for turn in reversed(turns):
        subj = str(turn.get("subject") or "")
        if not subj.upper().startswith("CHECKPOINT"):
            continue
        body = str(turn.get("body") or "")
        from .checkpoint_parse import parse_checkpoint

        try:
            parsed = parse_checkpoint(body)
        except Exception:  # noqa: BLE001
            continue
        if parsed.source_ref:
            return parsed.source_ref.lower()
    return None


__all__ = [
    "Attendance",
    "admission_mode_for_attendance",
    "admission_mode_for_root",
    "attendance_from_todo_attrs",
    "default_attendance_lookup",
]
