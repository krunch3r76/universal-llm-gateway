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

from universal_logging import get_logger

from ._shared import record
from .ops_audit_detectors import run_detectors

logger = get_logger(__name__)

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
    "panel_disposition_incomplete",
    # panel_falsifier_phase3_metric is cadence-scoped (§3.3 N≥20 cohort), not
    # per-close per-entity — excluded; cadence runner still TODO.
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
    except Exception as exc:
        record(
            "cortex.session.audit.degraded",
            session_id=session_id,
            error=str(exc) or type(exc).__name__,
        )
        logger.warning(
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

    Scope: the entities this session declares in ``entity_ids``. When ``entity_ids``
    is empty the gate audits nothing and returns {} (no scan) — graph-wide auditing
    is the user-callable ``session_audit`` / ``audit`` surface's job, not a per-close
    block/warn input. Preview (dry_run/preflight) and the real close share this
    scope, so a preview never diverges from the close it predicts (thread 1448).

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

    # Gate scope = the entities this session declares responsibility for. With
    # none declared, there is nothing for the *gate* to evaluate: the full-graph
    # audit is a user-callable / cadence concern (_op_session_audit, the `audit`
    # op), not a per-close block/warn input. Skip the scan — this also keeps
    # preview (dry_run / preflight) and the real close on identical scope, so a
    # preview never diverges from the close it predicts. See thread 1448.
    if not entity_ids:
        record(
            "cortex.session.audit.unscoped",
            session_id=session_id,
            agent=agent,
            reason="no_entity_ids",
        )
        return {}

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


def summarize_audit_outcome(
    outcome: dict[str, Any], *, sample_cap: int = 50
) -> dict[str, Any]:
    """Project a (possibly huge) audit outcome into a bounded response shape.

    Preserves block/warn semantics and counts; caps findings/criticals lists.
    """
    if not outcome:
        return outcome
    if outcome.get("blocked"):
        criticals = outcome.get("criticals", []) or []
        if len(criticals) <= sample_cap:
            return outcome
        return {
            **{k: v for k, v in outcome.items() if k != "criticals"},
            "criticals": criticals[:sample_cap],
            "criticals_total": len(criticals),
            "criticals_truncated": True,
        }
    if "warning" not in outcome:
        return outcome
    w = outcome["warning"]
    findings = w.get("audit_findings", []) or []
    by_severity: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    return {
        "warning": {
            "mode": w.get("mode"),
            "gap_count": w.get("gap_count", len(findings)),
            "deferred": w.get("deferred", []),
            "by_severity": by_severity,
            "by_kind": by_kind,
            "findings_sample": findings[:sample_cap],
            "findings_total": len(findings),
            "findings_truncated": len(findings) > sample_cap,
        }
    }


__all__ = [
    "_run_session_audit_graph_only",
    "_run_session_audit_or_block",
    "summarize_audit_outcome",
]
