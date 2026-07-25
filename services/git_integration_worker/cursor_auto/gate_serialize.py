"""Serialize nested cursor-sdk dispatches under ``cursor_sdk_gate`` limit=1.

When Auto holds the sole gate slot, a naive nested ``team_dispatch(cursor-sdk)``
self-deadlocks (friction 25956). Prefer nest+wait+park (``nest_under``) so the
parent parks, the child runs, then capacity restores — in-seat only when park is
unavailable or the work is trivial/bounded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from services.git_integration_worker.cursor_sdk_gate import sdk_dispatch_gate_stats


def should_run_in_seat(*, gate_limit: int | None = None) -> bool:
    """True when nested SDK admit would risk self-deadlock under limit=1.

    Prefer park (``nest_under``) when available. This helper remains for the
    in-seat fallback when park is unavailable or work is trivial.
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
    if work_bounded:
        return {
            "action": "in_seat",
            "reason": "bounded_work",
            "gate": gate,
        }
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


async def run_serialized[T](
    coro_factory: Callable[[], Awaitable[T]],
    *,
    prefer_in_seat: Callable[[], T] | None = None,
    work_bounded: bool = False,
    park_available: bool = True,
) -> tuple[T, dict[str, Any]]:
    """Run nested work via park preference; in-seat only as fallback.

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
