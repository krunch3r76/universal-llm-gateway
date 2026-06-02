"""GET /boot-audit-counters — graph audit severity counts for boot briefing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...dispatch_ops._shared import record
from ...dispatch_ops.ops_audit import _op_audit

router = APIRouter(tags=["boot"])


@router.get(
    "/boot-audit-counters",
    status_code=200,
    summary="Audit severity counts for boot briefing",
)
def boot_audit_counters() -> dict[str, Any]:
    """Return audit severity counts without the findings payload.

    Boot needs critical/warning/info tallies only. Full ``cortex(tool='audit')``
    returns every finding (~MB-scale); this route keeps the wire budget small.

    Runs the detector graph with ``emit=False`` so the boot path does not emit
    one ``cortex.audit.gap.detected`` event per gap (~17k/boot, write-amplifying
    the Event Service and breaching the INSPECT no-side-effects contract). A
    single ``cortex.audit.counts`` summary event is emitted instead.
    """
    result = _op_audit(emit=False)
    if "error" in result:
        # Omit count keys so boot extract_boot_results leaves audit_counters
        # None — the briefing card drops the audit section instead of 0/0/0.
        return {
            "unavailable": True,
            "error": result.get("error"),
        }
    counts = {
        "criticals": int(result.get("criticals", 0)),
        "warnings": int(result.get("warnings", 0)),
        "infos": int(result.get("infos", 0)),
        "gap_count": int(result.get("gap_count", 0)),
        "duration_ms": int(result.get("duration_ms", 0)),
    }
    record("cortex.audit.counts", **counts)
    return counts
