"""Cortex session audit gate — Layer 3 Phase 2.0 gate functions.

Contains:
  _run_session_audit_graph_only  — scoped graph-only run (single DB connection via subjects=)
  _run_session_audit_or_block    — gate called by _op_session_close (C3 invariant)

Split from ops_review.py to satisfy [quality:sloc] ≤300 per-file invariant.
See ops_review.py for the public user-callable ops (session_audit, case_audit, fill_gaps).

cortex.audit.completed is emitted only when findings exist — clean session closes
are silent to avoid event store noise at session_close frequency.
"""

from __future__ import annotations

import os
import time
from typing import Any

from ._shared import record
from .ops_audit_detectors import run_detectors

_AUDIT_MODE_ENV = "CORTEX_SESSION_AUDIT_MODE"
_PRE_CLOSE_GATE_KINDS = [
    "dangling_attribute_reference",
    "dangling_relationship_target",
    "entity_source_uri_missing",
    "entity_empty_description",
    "case_no_assertions",
    "case_no_relationships",
    "case_no_documents",
    "document_not_wired_to_case",
    "case_attribute_skill_dangling",
    "marker_nesting_violation",
    # Auditor-validatability gaps (Checks 4–5). Scoped to session entity_ids
    # when provided — prevents full-graph scan on every close. Warning severity
    # only; never critical, never blocking in WARN mode.
    "confirmed_entity_no_assertions",
    "confirmed_attribute_no_assertion",
]


def _run_session_audit_graph_only(
    session_id: str,
    entity_ids: list[str],
) -> list[dict[str, Any]]:
    """Run graph-only detectors scoped to session entities. Returns deduplicated findings.

    If entity_ids is non-empty, passes all subjects to run_detectors via subjects=
    for a single DB connection — avoids N open/close cycles per entity (W1 budget).
    If entity_ids is empty, scans the full graph (subject=None).
    Graph-only set (SQL-only) targets <100ms budget per W1.

    Degrades gracefully on detector errors (e.g. missing schema tables) — returns
    empty list so session_close is not blocked by audit infrastructure gaps.
    """
    kinds = list(_PRE_CLOSE_GATE_KINDS)
    try:
        if entity_ids:
            return run_detectors(
                kinds=kinds, subjects=list(entity_ids), include_filesystem=False
            )
        return run_detectors(kinds=kinds, subject=None, include_filesystem=False)
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "session audit degraded for %s — detector error suppressed",
            session_id,
            exc_info=True,
        )
        return []


def _run_session_audit_or_block(
    *,
    session_id: str,
    agent: str,
    entity_ids: list[str],
    defer_gaps: dict[str, str] | None,
) -> dict[str, Any]:
    """Session audit gate — called as first substantive step in _op_session_close (C3).

    Returns {} on clean pass. In WARN mode with findings returns {"warning": {...}}.
    In BLOCK mode with unresolved criticals returns {"blocked": True, ...} — caller
    MUST return this without writing any file or DB row.

    defer_gaps: {kind: reason} exempts specific gap kinds from blocking. Reasons are
    recorded in the event payload for audit provenance.

    cortex.audit.completed is only emitted when findings exist (after the early-return
    guard). Phase 2.1 note: cortex.session.audit.blocked uses role="coordination" —
    confirm record() shim supports per-call role override before enabling BLOCK mode.
    """
    mode = os.environ.get(_AUDIT_MODE_ENV, "warn").lower()
    start = time.time()

    findings = _run_session_audit_graph_only(session_id, entity_ids)
    duration_ms = int((time.time() - start) * 1000)

    if not findings:
        return {}

    deferred_kinds = set(defer_gaps or {})
    criticals = [
        f
        for f in findings
        if f["severity"] == "critical" and f["kind"] not in deferred_kinds
    ]

    record(
        "cortex.audit.completed",
        subject=session_id,
        gap_count=len(findings),
        criticals=len([f for f in findings if f["severity"] == "critical"]),
        warnings=len([f for f in findings if f["severity"] == "warning"]),
        infos=len([f for f in findings if f["severity"] == "info"]),
        kinds_run=list(_PRE_CLOSE_GATE_KINDS),
        duration_ms=duration_ms,
        include_filesystem=False,
    )

    record(
        "cortex.session.audit.gaps.observed",
        session_id=session_id,
        agent=agent,
        gap_count=len(findings),
        criticals=[{"kind": f["kind"], "subject": f["subject"]} for f in criticals],
        deferred=list(deferred_kinds) if deferred_kinds else [],
        mode=mode,
    )

    if mode == "block" and criticals:
        # Phase 2.1 — confirm role override support in record() before flipping env.
        record(
            "cortex.session.audit.blocked",
            session_id=session_id,
            criticals=[
                {"kind": f["kind"], "subject": f["subject"], "detail": f["detail"]}
                for f in criticals
            ],
        )
        return {
            "blocked": True,
            "error": "session_audit blocked close — critical gaps unresolved",
            "code": "session_audit_blocked",
            "criticals": criticals,
            "remedy": "Fix gaps or pass defer_gaps={kind: reason, ...}",
        }

    return {
        "warning": {
            "audit_findings": findings,
            "mode": mode,
            "gap_count": len(findings),
            "deferred": list(deferred_kinds) if deferred_kinds else [],
        }
    }


__all__ = [
    "_run_session_audit_graph_only",
    "_run_session_audit_or_block",
]
