"""§5.2 delivery table. Pure: no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["delivered", "retry", "harvest", "fallback"]

_REATTACH_FALLBACK = {
    "composer_dirty",
    "dormant_relaunch_failed",
    "bound_host_unlistable",
    "no_bound_or_dormant_seat",
}


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    action: Action
    delay_s: float = 0.0
    receipt: str | None = None
    error: str = ""


def classify_followup(
    *,
    timed_out: bool,
    body: dict[str, Any] | None,
) -> str:
    """Map a followup HTTP result onto a table row name."""
    if timed_out:
        return "indeterminate"
    if not isinstance(body, dict):
        return "other"
    error = str(body.get("error") or "")
    receipt = body.get("receipt")
    if error in {
        "lane_busy",
        "lane_not_attached",
        "lane_cse_ambiguous",
        "lane_cse_none",
        "operator_seat_mismatch",
        "seat_unavailable",
        *_REATTACH_FALLBACK,
    }:
        return error
    if body.get("ok") is True and receipt == "dom_committed":
        return "ok"
    if error == "send_unverified" or (
        body.get("ok") is False and receipt == "dom_paste"
    ):
        return "send_unverified" if error == "send_unverified" else "dom_paste"
    if error == "send_unverified":
        return "send_unverified"
    return "other"


def _count(last_error: str, token: str) -> int:
    if not last_error.startswith(token):
        return 0
    tail = last_error.split(":", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 1


def decide(
    kind: str,
    *,
    last_error: str = "",
    attempts: int = 0,
    harvest_present: bool | None = None,
) -> DeliveryDecision:
    """One row of the delivery table.

    ``harvest_present`` is set only after a harvest probe. ``None`` means the
    probe has not run; indeterminate classes return ``harvest`` first.
    """
    if attempts >= 6:
        return DeliveryDecision(action="fallback", error="attempt_ceiling")
    if kind == "ok":
        return DeliveryDecision(action="delivered", receipt="dom_committed")
    if kind == "lane_busy":
        seen = _count(last_error, "lane_busy")
        if seen < 3:
            return DeliveryDecision(
                action="retry", delay_s=15.0, error=f"lane_busy:{seen + 1}"
            )
        return DeliveryDecision(action="fallback", error="lane_busy")
    if kind in {"lane_not_attached", "lane_cse_ambiguous"}:
        if _count(last_error, kind) < 1:
            return DeliveryDecision(
                action="retry", delay_s=60.0, error=f"{kind}:1"
            )
        return DeliveryDecision(action="fallback", error=kind)
    if kind == "seat_unavailable":
        if _count(last_error, kind) < 1:
            return DeliveryDecision(
                action="retry", delay_s=30.0, error=f"{kind}:1"
            )
        return DeliveryDecision(action="fallback", error=kind)
    if kind in {"indeterminate", "send_unverified", "dom_paste"}:
        if harvest_present is None:
            return DeliveryDecision(action="harvest", error=kind)
        if harvest_present:
            return DeliveryDecision(action="delivered", receipt="harvest_marker")
        if _count(last_error, "harvest_miss") < 1:
            return DeliveryDecision(
                action="retry", delay_s=0.0, error="harvest_miss:1"
            )
        return DeliveryDecision(action="fallback", error=kind)
    return DeliveryDecision(action="fallback", error=kind or "other")
