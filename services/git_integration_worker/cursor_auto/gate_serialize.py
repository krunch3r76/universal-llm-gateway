"""Serialize nested cursor-sdk dispatches under ``cursor_sdk_gate`` limit=1.

When Auto holds the sole gate slot, a naive nested ``team_dispatch(cursor-sdk)``
self-deadlocks (friction 25956). Prefer in-seat for bounded work; otherwise
queue the nested admit until the holder releases — never block forever waiting
on a second concurrent SDK admit while holding the sole slot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from services.git_integration_worker.cursor_sdk_gate import sdk_dispatch_gate_stats


def should_run_in_seat(*, gate_limit: int | None = None) -> bool:
    """True when nested SDK admit would risk self-deadlock under limit=1.

    Returns True when the gate is already at capacity (active >= limit) — the
    caller should prefer in-seat work instead of nesting another SDK dispatch.
    """
    stats = sdk_dispatch_gate_stats()
    limit = gate_limit if gate_limit is not None else int(stats["limit"])
    return int(stats["active"]) >= limit


def plan_nested_dispatch(*, work_bounded: bool) -> dict[str, Any]:
    """Decide in-seat vs queue/serialize for a nested specialist dispatch.

    Returns a disposition plan (pure decision; does not acquire the gate).
    """
    stats = sdk_dispatch_gate_stats()
    active = int(stats["active"])
    limit = int(stats["limit"])
    queued = int(stats["queued"])
    if work_bounded or active >= limit:
        return {
            "action": "in_seat",
            "reason": (
                "bounded_work"
                if work_bounded
                else "gate_at_capacity_avoid_self_deadlock"
            ),
            "gate": {"active": active, "queued": queued, "limit": limit},
        }
    return {
        "action": "dispatch_now",
        "reason": "gate_has_capacity",
        "gate": {"active": active, "queued": queued, "limit": limit},
    }


async def run_serialized[T](
    coro_factory: Callable[[], Awaitable[T]],
    *,
    prefer_in_seat: Callable[[], T] | None = None,
    work_bounded: bool = False,
) -> tuple[T, dict[str, Any]]:
    """Run nested work in-seat when gate is full; otherwise run the coroutine.

    Does not acquire a second gate slot — callers that need a real nested
    ``team_dispatch`` must release the holder slot first or wait for capacity
    outside this helper. ``prefer_in_seat`` is the fallback for limit=1 holders.
    """
    plan = plan_nested_dispatch(work_bounded=work_bounded)
    if plan["action"] == "in_seat":
        if prefer_in_seat is None:
            raise RuntimeError(
                "cursor_auto gate_serialize: in_seat required but no "
                "prefer_in_seat fallback provided (friction 25956 class)"
            )
        return prefer_in_seat(), plan
    result = await coro_factory()
    return result, plan
