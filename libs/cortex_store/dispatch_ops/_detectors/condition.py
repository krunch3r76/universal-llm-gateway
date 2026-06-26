"""Condition stewardship audit detectors.

Four detectors per spec F6 (R5):
  suppressed_actionable_edge    — condition whose actionable edge has no live child todo
  stale_reveal_level            — safety_invariant/sensitive condition with reveal review >12 months old
  unresolved_safety_conflict    — logged CONFLICT sentinel never escalated
  advice_failure_recurrence     — repeat advice failures tied to a condition
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Any

from ._shared import _finding


def detect_suppressed_actionable_edge(
    conn: sqlite3.Connection, subject: str | None = None
) -> list[dict[str, Any]]:
    """Condition whose actionable edge (triggers relationship) has no live child todo.

    A ``blocked`` or ``recurrent_maintenance`` condition should always have at
    least one open/in_progress child todo reachable via a ``triggers``
    relationship. When the child todos have all been closed or the relationship
    was never created, the actionable need is silently suppressed.
    """
    findings: list[dict[str, Any]] = []
    try:
        where = "AND e.id = ?" if subject else ""
        params: tuple[Any, ...] = (subject,) if subject else ()
        rows = conn.execute(
            "SELECT e.id, e.name, json_extract(e.attributes, '$._admission_child_intent') AS ci "
            "FROM entities e "
            "WHERE e.type = 'condition' "
            "  AND e.workflow_state IN ('active', 'dormant') "
            "  AND json_extract(e.attributes, '$._admission_child_intent') IS NOT NULL "
            + where,
            params,
        ).fetchall()
        for row in rows:
            entity_id = row[0]
            # Check if any live child todo exists via a `triggers` relationship
            live_children = conn.execute(
                "SELECT 1 FROM relationships r "
                "JOIN entities t ON t.id = r.target_id "
                "WHERE r.source_id = ? AND r.type = 'triggers' "
                "  AND t.type = 'todo' "
                "  AND t.workflow_state NOT IN ('done', 'cancelled') "
                "LIMIT 1",
                (entity_id,),
            ).fetchone()
            if not live_children:
                findings.append(
                    _finding(
                        "suppressed_actionable_edge",
                        entity_id,
                        f"Condition {entity_id!r} has child_intent={row[2]!r} but no live child todo via triggers relationship.",
                    )
                )
    except Exception:
        pass
    return findings


def detect_stale_reveal_level(
    conn: sqlite3.Connection, subject: str | None = None
) -> list[dict[str, Any]]:
    """safety_invariant or sensitive condition with reveal review >12 months old.

    The ``reveal_reviewed_at`` attribute records the last time the operator
    confirmed the redaction level. For high-sensitivity conditions, a stale
    review creates a risk that the sensitivity classification is outdated.
    """
    findings: list[dict[str, Any]] = []
    cutoff = (datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=365)).isoformat()
    try:
        where = "AND e.id = ?" if subject else ""
        params: tuple[Any, ...] = (subject,) if subject else ()
        rows = conn.execute(
            "SELECT e.id, "
            "  json_extract(e.attributes, '$.safety_invariant') AS si, "
            "  json_extract(e.attributes, '$.reveal_default') AS rd, "
            "  json_extract(e.attributes, '$.reveal_reviewed_at') AS rra "
            "FROM entities e "
            "WHERE e.type = 'condition' "
            "  AND e.workflow_state IN ('active', 'dormant') "
            "  AND (json_extract(e.attributes, '$.safety_invariant') = 1 "
            "    OR json_extract(e.attributes, '$.reveal_default') IN ('sensitive', 'restricted')) "
            + where,
            params,
        ).fetchall()
        for row in rows:
            entity_id, _, _, reviewed_at = row
            if reviewed_at is None or str(reviewed_at) < cutoff:
                findings.append(
                    _finding(
                        "stale_reveal_level",
                        entity_id,
                        f"High-sensitivity condition {entity_id!r} reveal_reviewed_at={reviewed_at!r} is >12mo old or absent.",
                    )
                )
    except Exception:
        pass
    return findings


def detect_unresolved_safety_conflict(
    conn: sqlite3.Connection, subject: str | None = None
) -> list[dict[str, Any]]:
    """A logged CONFLICT sentinel that was never escalated.

    When a condition_redaction CONFLICT is logged (stored as an assertion or
    attribute on the condition entity), it must be escalated by the
    orchestrator/lead. Unresolved conflicts accumulate as a stewardship gap.
    """
    findings: list[dict[str, Any]] = []
    try:
        where = "AND e.id = ?" if subject else ""
        params: tuple[Any, ...] = (subject,) if subject else ()
        rows = conn.execute(
            "SELECT e.id "
            "FROM entities e "
            "WHERE e.type = 'condition' "
            "  AND json_extract(e.attributes, '$.unresolved_conflict') = 1 "
            + where,
            params,
        ).fetchall()
        for row in rows:
            entity_id = row[0]
            findings.append(
                _finding(
                    "unresolved_safety_conflict",
                    entity_id,
                    f"Condition {entity_id!r} has an unresolved CONFLICT sentinel; escalate to orchestrator/lead.",
                )
            )
    except Exception:
        pass
    return findings


def detect_advice_failure_recurrence(
    conn: sqlite3.Connection, subject: str | None = None
) -> list[dict[str, Any]]:
    """Repeat advice failures tied to a condition entity.

    When a condition drives repeated advice failures (tracked via
    ``advice_failure_count`` attribute), it warrants a stewardship review
    to determine whether the redaction level or condition framing is impeding
    effective guidance.
    """
    findings: list[dict[str, Any]] = []
    _ADVICE_FAILURE_THRESHOLD = 2
    try:
        where = "AND e.id = ?" if subject else ""
        params: tuple[Any, ...] = (subject,) if subject else ()
        rows = conn.execute(
            "SELECT e.id, json_extract(e.attributes, '$.advice_failure_count') AS afc "
            "FROM entities e "
            "WHERE e.type = 'condition' "
            "  AND json_extract(e.attributes, '$.advice_failure_count') IS NOT NULL "
            + where,
            params,
        ).fetchall()
        for row in rows:
            entity_id, count = row
            if count and int(count) >= _ADVICE_FAILURE_THRESHOLD:
                findings.append(
                    _finding(
                        "advice_failure_recurrence",
                        entity_id,
                        f"Condition {entity_id!r} advice_failure_count={count} >= threshold={_ADVICE_FAILURE_THRESHOLD}.",
                    )
                )
    except Exception:
        pass
    return findings


__all__ = [
    "detect_advice_failure_recurrence",
    "detect_stale_reveal_level",
    "detect_suppressed_actionable_edge",
    "detect_unresolved_safety_conflict",
]
