"""Close persistent auto-provisioned generate threads when the result turn is read."""

from __future__ import annotations

from typing import Any, Literal

from .db.threads import get_thread, get_thread_turns_asc
from .db.threads_atomic import close_thread
from .disposition import (
    _closeout_land_meter_from_turns,
    agents_match,
    resolve_bus_lifecycle,
    summary_for_auto_close,
)
from .turns_models import ThreadStatus

CLOSE_ON_READ_TAG = "dispatch:close_on_read"
_TYPE_GENERATE_TAG = "type:generate"
CloseContract = Literal["lead", "auto"]


def terminate_bus_lifecycle_for_close_contract(
    close_contract: CloseContract | None,
) -> Literal["persistent", "ephemeral"] | None:
    """Explicit terminate override — ``lead`` keeps the thread active post-closeout."""
    if close_contract == "lead":
        return "persistent"
    return None


def append_close_on_read_marker(
    tags: list[str],
    *,
    bus_lifecycle: str | None = None,
    close_contract: CloseContract | None = None,
) -> list[str]:
    """Tag auto-provisioned persistent generate consult threads for close-on-read."""
    if close_contract == "lead":
        return list(tags)
    effective = list(tags)
    lifecycle = resolve_bus_lifecycle(
        effective,
        explicit=bus_lifecycle,  # type: ignore[arg-type]
    )
    if (
        _TYPE_GENERATE_TAG in effective
        and lifecycle == "persistent"
        and CLOSE_ON_READ_TAG not in effective
    ):
        effective.append(CLOSE_ON_READ_TAG)
    return effective


def _agents_match(left: str | None, right: str | None) -> bool:
    return agents_match(left, right)


def _find_on_behalf_result_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(turns) < 2:
        return None
    pointer = turns[0]
    role = pointer.get("to_agent")
    caller = pointer.get("from_agent")
    if not role or not caller:
        return None
    for turn in reversed(turns[1:]):
        turn_number = int(turn.get("turn_number") or 0)
        if turn_number < 2:
            continue
        if _agents_match(str(turn.get("from_agent")), str(role)) and _agents_match(
            str(turn.get("to_agent")), str(caller)
        ):
            return turn
    return None


def _thread_eligible_for_close_on_read(thread: dict[str, Any]) -> bool:
    if thread.get("status") == ThreadStatus.CLOSED:
        return False
    tags = thread.get("tags") or []
    if CLOSE_ON_READ_TAG not in tags:
        return False
    if resolve_bus_lifecycle(tags) != "persistent":
        return False
    if thread.get("bus_lifecycle_state") != "active":
        return False
    return True


def maybe_close_generate_thread_on_read(thread_id: str) -> dict[str, Any] | None:
    """Close a marked generate thread once its on-behalf result turn has been read.

    Guardrails (AC3):
    - Only threads tagged ``dispatch:close_on_read`` with persistent lifecycle.
    - Only when the on-behalf result turn (turn >= 2, from role to caller) is read.
    - No later unread turns may exist.
    - Pointer-turn reads alone never satisfy the result-turn predicate.
    """
    thread = get_thread(thread_id)
    if thread is None or not _thread_eligible_for_close_on_read(thread):
        return None

    turns = get_thread_turns_asc(thread_id)
    result_turn = _find_on_behalf_result_turn(turns)
    if result_turn is None or result_turn.get("read_at") is None:
        return None

    result_number = int(result_turn["turn_number"])
    if any(
        t.get("read_at") is None and int(t.get("turn_number") or 0) > result_number
        for t in turns
    ):
        return None

    tags = thread.get("tags") or []
    landed, commits_ahead = _closeout_land_meter_from_turns(turns)
    summary = summary_for_auto_close(
        thread.get("summary"),
        tags=tags,
        landed=landed,
        commits_ahead=commits_ahead,
    )
    return close_thread(
        thread_id,
        summary=summary,
        mark_all_read=True,
        lifecycle_trigger="close_on_read",
    )
