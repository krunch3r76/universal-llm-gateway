"""Liveness + age discrimination for charter-tick SOS (a:26885).

Ports the gate-defer G1/G2/G3 shape onto root-skip: live-backed sticky
NOOPs must not count-escalate; orphan holders map to ``FireAttemptOutcome.INTEGRITY``;
every skip ages via the durable ``ledger_age`` tick_stall clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .ledger_age import (
    AgeWatchResult,
)
from .ledger_age import (
    clear as age_watch_clear,
)
from .ledger_age import (
    observe as age_watch_observe,
)

StickyBacking = Literal["live", "orphan", "none"]


@dataclass(frozen=True, slots=True)
class SkipLivenessVerdict:
    """How one skip observation should affect SOS fire policy."""

    sticky_backing: StickyBacking
    holder_dispatch_id: str | None
    age: AgeWatchResult | None
    suppress_count_escalate: bool
    force_immediate: bool
    immediate_reason: str | None


def _holder_is_live(holder_dispatch_id: str, payload: dict[str, Any]) -> bool:
    for op in payload.get("active_ops") or []:
        if isinstance(op, dict) and str(op.get("op_id") or "") == holder_dispatch_id:
            return True
    cursor = payload.get("cursor_dispatches")
    if isinstance(cursor, dict):
        for raw in cursor.get("dispatch_ids") or []:
            if str(raw) == holder_dispatch_id:
                return True
    return False


def classify_sticky_backing(payload: dict[str, Any] | None) -> tuple[StickyBacking, str | None]:
    """Return live/orphan/none for the GIW write-lease ∪ live cursor ops."""
    if payload is None:
        return "none", None
    lease = payload.get("write_lease")
    holder_id: str | None = None
    if isinstance(lease, dict):
        raw = str(lease.get("holder_dispatch_id") or "").strip()
        holder_id = raw or None
        if holder_id:
            if not _holder_is_live(holder_id, payload):
                return "orphan", holder_id
            return "live", holder_id
    if _any_live_cursor_op(payload):
        return "live", holder_id
    return "none", holder_id


def _any_live_cursor_op(payload: dict[str, Any]) -> bool:
    for op in payload.get("active_ops") or []:
        if not isinstance(op, dict):
            continue
        if str(op.get("kind") or "") == "cursor_sdk" and str(op.get("op_id") or ""):
            return True
    cursor = payload.get("cursor_dispatches")
    if isinstance(cursor, dict):
        ids = cursor.get("dispatch_ids") or []
        if isinstance(ids, list) and any(str(x).strip() for x in ids):
            return True
    return False


def verdict_for_skip(
    *,
    sos_reason: str | None,
    skipped_reason: str | None,
    root_id: str,
    giw_payload: dict[str, Any] | None,
    admitted: bool,
) -> SkipLivenessVerdict:
    """Combine sticky liveness with durable tick_stall age for one observation."""
    if admitted:
        age_watch_clear("tick_stall", root_id)
        return SkipLivenessVerdict(
            sticky_backing="none",
            holder_dispatch_id=None,
            age=None,
            suppress_count_escalate=True,
            force_immediate=False,
            immediate_reason=None,
        )

    backing, holder_id = classify_sticky_backing(giw_payload)
    is_sticky = sos_reason == "sticky_admitted"

    if is_sticky and backing == "orphan":
        return SkipLivenessVerdict(
            sticky_backing=backing,
            holder_dispatch_id=holder_id,
            age=None,
            suppress_count_escalate=False,
            force_immediate=True,
            immediate_reason="orphan_holder_no_live_backing",
        )

    if is_sticky and backing == "live":
        # Healthy in-flight window — do not count-escalate or age-stall.
        age_watch_clear("tick_stall", root_id)
        return SkipLivenessVerdict(
            sticky_backing=backing,
            holder_dispatch_id=holder_id,
            age=None,
            suppress_count_escalate=True,
            force_immediate=False,
            immediate_reason=None,
        )

    # Any non-admitted skip (incl. empty_hopper / quiet classify=None) ages.
    present = True
    age = age_watch_observe("tick_stall", root_id, present=present)
    force_age = age.outcome == "escalate"
    return SkipLivenessVerdict(
        sticky_backing=backing if is_sticky else "none",
        holder_dispatch_id=holder_id,
        age=age,
        suppress_count_escalate=False,
        force_immediate=force_age,
        immediate_reason="skip_age_exceeded" if force_age else None,
    )


__all__ = [
    "SkipLivenessVerdict",
    "StickyBacking",
    "classify_sticky_backing",
    "verdict_for_skip",
]
