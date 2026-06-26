"""Session-mode escalation trigger computation (cortex durable state only).

Implements F4 from the condition entity-type spec (design:session-mode,
assertion 20686, panel thread 3279).

Cortex computes the MANDATORY escalation trigger set from durable state and
surfaces ``force_prompt=True`` when any trigger is present. Per-turn tone
inference is explicitly a lead-runtime/prompt behaviour and OUT OF SCOPE here.

Trigger sources:
  T1  Any active ``safety_invariant`` condition entity
  T2  Legal or financial deadline entity within the configured warning window
  T3  Irreversible-harm flag set in durable session context
  T4  Repeated inferred-vs-actual posture mismatch counter exceeds threshold

An explicit user posture declaration (supplied via ``explicit_posture``)
overrides the inferred default posture but does NOT suppress ``force_prompt``
if mandatory triggers are present.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .db import query
from .workflow_state import closure_audit_exempt

_DEADLINE_WARNING_DAYS = 30
_MISMATCH_THRESHOLD = 3

_LEGAL_FINANCIAL_TYPES = frozenset({"legal_matter", "deadline", "tax_deadline", "legal_document"})


@dataclass
class SessionTriggers:
    """Output from ``compute_session_triggers``."""

    triggers: list[dict[str, Any]] = field(default_factory=list)
    force_prompt: bool = False
    explicit_posture_override: str | None = None


def _active_safety_invariant_conditions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all active entities of type ``condition`` with safety_invariant=true."""
    try:
        rows = query(
            conn,
            "SELECT id, name, workflow_state, attributes "
            "FROM entities "
            "WHERE type = 'condition' "
            "  AND workflow_state = 'active' "
            "  AND json_extract(attributes, '$.safety_invariant') = 1",
            (),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def _upcoming_legal_financial_deadlines(
    conn: sqlite3.Connection, *, warning_days: int = _DEADLINE_WARNING_DAYS
) -> list[dict[str, Any]]:
    """Return legal/financial deadline entities within the warning window.

    Uses the workflow_state column (not terminal) and attributes.due_date or
    attributes.deadline_date. Falls back gracefully if the column is absent.
    """
    try:
        rows = query(
            conn,
            "SELECT id, name, type, workflow_state, attributes "
            "FROM entities "
            "WHERE type IN ('legal_matter', 'deadline', 'tax_deadline', 'legal_document') "
            "  AND (workflow_state IS NULL OR workflow_state NOT IN ('closed', 'void', 'paid', 'filed')) "
            "  AND ("
            "    (json_extract(attributes, '$.due_date') IS NOT NULL AND "
            "     julianday(json_extract(attributes, '$.due_date')) - julianday('now') BETWEEN 0 AND ?) "
            "    OR "
            "    (json_extract(attributes, '$.deadline_date') IS NOT NULL AND "
            "     julianday(json_extract(attributes, '$.deadline_date')) - julianday('now') BETWEEN 0 AND ?)"
            "  )",
            (warning_days, warning_days),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def _irreversible_harm_flag(conn: sqlite3.Connection) -> bool:
    """True if any session-context entity carries an irreversible_harm flag."""
    try:
        rows = query(
            conn,
            "SELECT 1 FROM entities "
            "WHERE json_extract(attributes, '$.irreversible_harm') = 1 LIMIT 1",
            (),
        )
        return bool(rows)
    except Exception:
        return False


def _posture_mismatch_count(conn: sqlite3.Connection) -> int:
    """Count of inferred-vs-actual posture mismatches from session journals.

    Reads the ``posture_mismatch_count`` attribute from the most recent
    session entity. Returns 0 when absent or unreadable.
    """
    try:
        rows = query(
            conn,
            "SELECT json_extract(attributes, '$.posture_mismatch_count') AS cnt "
            "FROM entities "
            "WHERE type = 'transcript' "
            "ORDER BY created_at DESC LIMIT 1",
            (),
        )
        if rows:
            val = rows[0]["cnt"]
            if val is not None:
                return int(val)
    except Exception:
        pass
    return 0


def compute_session_triggers(
    conn: sqlite3.Connection,
    *,
    explicit_posture: str | None = None,
    warning_days: int = _DEADLINE_WARNING_DAYS,
    mismatch_threshold: int = _MISMATCH_THRESHOLD,
) -> SessionTriggers:
    """Compute mandatory escalation triggers from durable cortex state.

    Args:
        conn: open cortex DB connection.
        explicit_posture: when the user has explicitly declared their posture
            (e.g. "planning" | "venting" | "update"), this overrides the
            inferred default. Does NOT suppress force_prompt.
        warning_days: horizon for the legal/financial deadline trigger.
        mismatch_threshold: number of accumulated posture mismatches before T4 fires.

    Returns a SessionTriggers dataclass with:
        triggers  — list of active trigger dicts (type, entity_id?, detail)
        force_prompt — True whenever any trigger is present
        explicit_posture_override — passed through for surface consumers
    """
    triggers: list[dict[str, Any]] = []

    # T1: active safety_invariant conditions
    for cond in _active_safety_invariant_conditions(conn):
        triggers.append(
            {
                "type": "T1_safety_invariant_condition",
                "entity_id": cond.get("id"),
                "entity_name": cond.get("name"),
                "detail": "Active safety_invariant condition present; stewardship confirmation required.",
            }
        )

    # T2: upcoming legal/financial deadlines
    for dl in _upcoming_legal_financial_deadlines(conn, warning_days=warning_days):
        triggers.append(
            {
                "type": "T2_legal_financial_deadline",
                "entity_id": dl.get("id"),
                "entity_name": dl.get("name"),
                "detail": f"Legal/financial deadline entity {dl.get('id')!r} within {warning_days}-day window.",
            }
        )

    # T3: irreversible harm flag
    if _irreversible_harm_flag(conn):
        triggers.append(
            {
                "type": "T3_irreversible_harm_flag",
                "entity_id": None,
                "detail": "Irreversible-harm flag set in durable session context.",
            }
        )

    # T4: repeated posture mismatch
    mismatch_count = _posture_mismatch_count(conn)
    if mismatch_count >= mismatch_threshold:
        triggers.append(
            {
                "type": "T4_posture_mismatch_recurrence",
                "entity_id": None,
                "detail": (
                    f"Posture mismatch counter={mismatch_count} >= threshold={mismatch_threshold}. "
                    "Explicit posture check recommended before proceeding."
                ),
            }
        )

    return SessionTriggers(
        triggers=triggers,
        force_prompt=bool(triggers),
        explicit_posture_override=explicit_posture,
    )


__all__ = ["SessionTriggers", "compute_session_triggers"]
