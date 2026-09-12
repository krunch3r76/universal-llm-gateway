"""When a liaison hop is allowed.

Operator 2026-09-12 07:26 PT: hop only when something needs autonomous
follow-up. Empty NOW and quiet ticks are STAY. A HOLD_MERGE *row* still
refuses the hop (do not mill LAND OWED) — autonomous land is mandated
unless the operator explicitly asked to hold (2026-09-12 07:33 PT); the
seat lands in-tab, it does not hop. ``OPERATOR_GATE`` in the NOW string
is a hop-stay so the successor does not mill a parked row — it is **not**
a human page. The seat rewrites NOW to the next objective and continues.
"""

from __future__ import annotations

from typing import Any

HOLD_MARKERS = ("HOLD_MERGE", "LAND OWED", "OPERATOR_GATE")


def hop_qualifies(
    *,
    row: str,
    arm_labels: list[str] | None = None,
    context_budget: bool = False,
) -> dict[str, Any]:
    """Return ``{ok, reason}``. Fail closed: refuse unless follow-up is named.

    Qualifies: live watcher tails, CONTEXT_BUDGET with a non-hold NOW, or a
    NOW that is not HOLD_MERGE / LAND OWED / OPERATOR_GATE / empty / quiet.
    ``--force`` on the hop script is the operator override, not this function.

    ``arm_labels`` must already exclude pollers on the caller's own lane
    (``ide_hop.live_watcher_labels(exclude_threads=…)``); a seat waiting on its
    own closeout would otherwise qualify forever.
    """
    labels = [str(x) for x in (arm_labels or []) if str(x).strip()]
    if labels:
        return {"ok": True, "reason": "live_watcher"}
    text = (row or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_now"}
    upper = text.upper()
    if "OPERATOR_GATE" in upper:
        return {"ok": False, "reason": "operator_gate"}
    if any(marker in upper for marker in HOLD_MARKERS):
        return {"ok": False, "reason": "hold_merge"}
    if context_budget:
        return {"ok": True, "reason": "context_budget"}
    if _quiet_row(text):
        return {"ok": False, "reason": "no_autonomous_followup"}
    return {"ok": True, "reason": "dispatchable_now"}


def _quiet_row(text: str) -> bool:
    """Plan-from-digest / parked-only rows are not follow-up."""
    compact = " ".join(text.lower().split())
    return compact in {"none", "quiet", "empty"} or compact.startswith("arm: none")
