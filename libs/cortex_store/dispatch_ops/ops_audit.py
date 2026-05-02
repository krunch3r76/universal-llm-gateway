"""Cortex audit op — Layer 2 dispatcher (Phase 1b of cortex-graph-projection-and-audit-primitives).

Per v2 plan §6: dispatches to detectors in ops_audit_detectors.py, buckets graph-only (default for session_audit) vs fs-touching (opt-in), enforces budgets (<100ms graph, <2s fs), emits cortex.audit.* signals with kind-in-payload (C2).

subject can be entity_id, "case:xxx", or None (all).

See §1 ship gate for HEI fixture test, §8 for performance, §2 for file split.
"""

from __future__ import annotations

import time
from typing import Any

from ._shared import record
from .ops_audit_detectors import (
    ALL_KINDS,
    FS_TOUCHING_KINDS,
    GRAPH_ONLY_KINDS,
    INFO_KINDS,
    run_detectors,
)


def _op_audit(
    subject: str | None = None,
    kinds: list[str] | None = None,
    include_filesystem: bool = False,
    **_: object,
) -> dict[str, Any]:
    """Run audit detectors for a subject (entity, case, or all).

    - kinds=None → graph-only set by default (W1 for session_audit).
    - include_filesystem=true → adds the 4 fs-touching detectors.
    - Returns {findings: [...], gap_count, criticals, warnings, infos, duration_ms, kinds_run}.
    - Emits cortex.audit.completed and per-gap cortex.audit.gap.detected (kind in payload).
    - Budget enforced via timing; WARN event if exceeded.
    """
    if kinds and not all(k in ALL_KINDS for k in kinds):
        return {
            "error": f"Invalid kind(s). Valid: {sorted(ALL_KINDS)}",
            "code": "invalid_audit_kind",
        }

    start = time.time()
    findings = run_detectors(
        kinds=kinds, subject=subject, include_filesystem=include_filesystem
    )

    duration_ms = int((time.time() - start) * 1000)
    criticals = [f for f in findings if f["severity"] == "critical"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    infos = [f for f in findings if f["severity"] == "info"]
    if kinds is not None:
        kinds_run = [k for k in kinds if k in ALL_KINDS]
    else:
        kinds_run = list(GRAPH_ONLY_KINDS) + list(INFO_KINDS)
        if include_filesystem:
            kinds_run = kinds_run + list(FS_TOUCHING_KINDS)

    # Emit events per plan §6 (using existing record() shim per change-scope)
    record(
        "cortex.audit.completed",
        subject=subject or "all",
        gap_count=len(findings),
        criticals=len(criticals),
        warnings=len(warnings),
        infos=len(infos),
        kinds_run=kinds_run,
        duration_ms=duration_ms,
        include_filesystem=include_filesystem,
    )

    for f in findings:
        record(
            "cortex.audit.gap.detected",
            kind=f["kind"],
            subject=f["subject"],
            severity=f["severity"],
            detail=f["detail"],
            audit_id=f["audit_id"],
        )

    if duration_ms > (100 if not include_filesystem else 2000):
        record(
            "cortex.audit.budget.exceeded",
            duration_ms=duration_ms,
            budget_ms=100 if not include_filesystem else 2000,
            subject=subject,
        )

    return {
        "findings": findings,
        "gap_count": len(findings),
        "criticals": len(criticals),
        "warnings": len(warnings),
        "infos": len(infos),
        "duration_ms": duration_ms,
        "kinds_run": kinds_run,
        "_next": "use case_audit or session_audit for full workflows",
    }


__all__ = ["_op_audit"]
