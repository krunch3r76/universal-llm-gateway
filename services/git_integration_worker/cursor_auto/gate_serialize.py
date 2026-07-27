"""Serialize nested cursor-sdk dispatches under ``cursor_sdk_gate`` limit=1.

When Auto holds the sole gate slot, a naive nested ``team_dispatch(cursor-sdk)``
self-deadlocks (friction 25956). Prefer nest+wait+park (``nest_under``) so the
parent parks, the child runs, then capacity restores. The cursor-auto handler
refuses nested ``in_seat`` plans with ``nested_in_seat_unsupported`` and does
not call ``prefer_in_seat``; ``run_serialized`` remains a helper for other
callers that may supply ``prefer_in_seat``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from services.git_integration_worker.cursor_sdk_gate import sdk_dispatch_gate_stats

NESTED_IN_SEAT_REASON = "nested_in_seat_unsupported"


def should_run_in_seat(*, gate_limit: int | None = None) -> bool:
    """True when nested SDK admit would risk self-deadlock under limit=1.

    Prefer park (``nest_under``) when available. The cursor-auto handler maps
    nested ``in_seat`` to ``needs-attended`` (``nested_in_seat_unsupported``)
    rather than executing in-process.
    """
    stats = sdk_dispatch_gate_stats()
    limit = gate_limit if gate_limit is not None else int(stats["limit"])
    return int(stats["active"]) >= limit


def plan_nested_dispatch(
    *, work_bounded: bool, park_available: bool = True
) -> dict[str, Any]:
    """Decide nest+park vs in-seat vs dispatch-now for a nested specialist.

    Returns a disposition plan (pure decision; does not acquire the gate).
    """
    stats = sdk_dispatch_gate_stats()
    active = int(stats["active"])
    limit = int(stats["limit"])
    queued = int(stats["queued"])
    gate = {"active": active, "queued": queued, "limit": limit}
    if active >= limit:
        if park_available:
            return {
                "action": "nest_park",
                "reason": "gate_at_capacity_prefer_park",
                "gate": gate,
            }
        return {
            "action": "in_seat",
            "reason": "gate_at_capacity_park_unavailable",
            "gate": gate,
        }
    return {
        "action": "dispatch_now",
        "reason": "gate_has_capacity",
        "gate": gate,
    }


def prefer_dispatch_over_park(
    gate_plan: dict[str, Any], *, work_bounded: bool
) -> dict[str, Any]:
    """Holderless bounded work dispatches instead of terminal park (5968 #4).

    When a ledger holder exists, bounded work at capacity keeps ``nest_park`` so
    the parent can park under the external/peer holder. Only the holderless case
    upgrades to ``dispatch_now``.
    """
    if not work_bounded:
        return gate_plan
    action = gate_plan.get("action")
    reason = str(gate_plan.get("reason") or "")
    if action in {"in_seat", "nest_park"} and reason in {
        "gate_at_capacity_prefer_park",
        "gate_at_capacity_park_unavailable",
        "nest_park_without_holder",
    }:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        snap = CursorDispatchLedger.instance().lease_snapshot()
        if snap.get("holder_dispatch_id"):
            return gate_plan
        return {
            **gate_plan,
            "action": "dispatch_now",
            "reason": "holderless_bounded_prefer_dispatch",
            "overrode": action,
        }
    return gate_plan


async def run_serialized[T](
    coro_factory: Callable[[], Awaitable[T]],
    *,
    prefer_in_seat: Callable[[], T] | None = None,
    work_bounded: bool = False,
    park_available: bool = True,
) -> tuple[T, dict[str, Any]]:
    """Run nested work via park preference; optional in-seat via ``prefer_in_seat``.

    The cursor-auto handler does not call this helper; nested ``in_seat`` there
    terminates as ``nested_in_seat_unsupported``. Other callers may supply
    ``prefer_in_seat`` when ``plan_nested_dispatch`` yields ``in_seat``.

    Does not itself perform nest_under admit — callers that need a real nested
    ``team_dispatch`` must pass ``nest_under=<parent_dispatch_id>`` on the GIW
    payload (or rely on Stargate automatic nest wiring).
    """
    plan = plan_nested_dispatch(
        work_bounded=work_bounded, park_available=park_available
    )
    if plan["action"] == "in_seat":
        if prefer_in_seat is None:
            raise RuntimeError(
                "cursor_auto gate_serialize: in_seat required but no "
                "prefer_in_seat fallback provided (friction 25956 class)"
            )
        return prefer_in_seat(), plan
    if plan["action"] == "nest_park":
        # Caller must fire team_dispatch with nest_under; this helper cannot
        # invent the parent id. Fall through to coro when provided.
        result = await coro_factory()
        return result, plan
    result = await coro_factory()
    return result, plan
