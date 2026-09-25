"""When a liaison hop is allowed.

``hop_qualifies`` is an advisory predicate over caller-supplied arguments; it is
not a hop precondition. The keystroke precondition is ``fire_ide_hop(seal=…)``,
which refuses unless the CHECKPOINT seal receipt is ok.

Operator 2026-09-12 07:26 PT: hop only when something needs autonomous
follow-up. Empty NOW and quiet ticks are STAY. A HOLD_MERGE *row* still
refuses the hop (do not mill LAND OWED) — autonomous land is mandated
unless the operator explicitly asked to hold (2026-09-12 07:33 PT); the
seat lands in-tab, it does not hop.

Operator 2026-09-15 (a:34092): ``OPERATOR_GATE`` is a typed stop with
``source=operator`` in policy — not a substring of ``now_row``. A seat
writing gate prose into ``now_row`` does not arm or block; only
``liaison-tick.py --operator-gate …`` arms it (page + park that row). The
shared ``--set`` channel refuses the key — successors reach only ``--set``.
"""

from __future__ import annotations

from typing import Any

from bus_watch.liaison_stops import operator_gate_armed

HOLD_MARKERS = ("HOLD_MERGE", "LAND OWED")


def hop_qualifies(
    *,
    row: str,
    arm_labels: list[str] | None = None,
    context_budget: bool = False,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``{ok, reason}``. Advisory only — not enforced at keystroke.

    This predicate is advisory over the arguments the hop script supplies; the
    hard hop precondition is ``fire_ide_hop(seal=…)`` (seal receipt must be ok).
    Fail closed here: refuse unless follow-up is named.

    Qualifies: live watcher tails, CONTEXT_BUDGET with a non-hold NOW, or a
    NOW that is not HOLD_MERGE / LAND OWED / empty / quiet. An operator-sourced
    ``policy.operator_gate`` (``source=operator``, ``value=true``) refuses with
    ``reason=operator_gate`` — seat-authored gate text in ``row`` is ignored.
    ``--force`` on the hop script is the operator override, not this function.

    ``arm_labels`` must already exclude pollers on the caller's own lane
    (``ide_hop.live_watcher_labels(exclude_threads=…)``); a seat waiting on its
    own closeout would otherwise qualify forever.
    """
    labels = [str(x) for x in (arm_labels or []) if str(x).strip()]
    if labels:
        return {"ok": True, "reason": "live_watcher"}
    if operator_gate_armed(policy or {}):
        return {"ok": False, "reason": "operator_gate"}
    text = (row or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_now"}
    upper = text.upper()
    if any(marker in upper for marker in HOLD_MARKERS):
        return {"ok": False, "reason": "hold_merge"}
    if context_budget:
        return {"ok": True, "reason": "context_budget"}
    if quiet_row(text):
        return {"ok": False, "reason": "no_autonomous_followup"}
    return {"ok": True, "reason": "dispatchable_now"}


def quiet_row(text: str) -> bool:
    """Plan-from-digest / parked-only rows are not follow-up."""
    compact = " ".join(text.lower().split())
    return compact in {"none", "quiet", "empty"} or compact.startswith("arm: none")
