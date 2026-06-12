"""Bus-thread disposition — ephemeral auto-close by default."""

from __future__ import annotations

from typing import Any, Literal

from agent_seat.registry import normalize_agent_slug

from .db.threads import get_thread, get_thread_turns_asc
from .db.threads_atomic import close_thread
from .turns_models import ThreadStatus

BusLifecycle = Literal["persistent", "ephemeral"]
_LIFECYCLE_EPHEMERAL = "bus_lifecycle:ephemeral"
_LIFECYCLE_PERSISTENT = "bus_lifecycle:persistent"


def resolve_bus_lifecycle(
    tags: list[str] | None,
    *,
    explicit: BusLifecycle | None = None,
) -> BusLifecycle:
    if explicit == "persistent":
        return "persistent"
    if explicit == "ephemeral":
        return "ephemeral"
    if tags and _LIFECYCLE_PERSISTENT in tags:
        return "persistent"
    return "ephemeral"


def bus_lifecycle_tag(lifecycle: BusLifecycle) -> str:
    return _LIFECYCLE_EPHEMERAL if lifecycle == "ephemeral" else _LIFECYCLE_PERSISTENT


def append_bus_lifecycle_tags(
    tags: list[str] | None,
    *,
    bus_lifecycle: BusLifecycle = "ephemeral",
) -> list[str]:
    effective = list(tags or [])
    for tag in (_LIFECYCLE_EPHEMERAL, _LIFECYCLE_PERSISTENT):
        if tag in effective:
            effective.remove(tag)
    effective.append(bus_lifecycle_tag(bus_lifecycle))
    return effective


def maybe_auto_close_after_dispatch_terminate(
    thread_id: str,
    *,
    terminal_status: str,
    explicit_bus_lifecycle: BusLifecycle | None = None,
) -> dict[str, Any] | None:
    if terminal_status != "completed":
        return None
    thread = get_thread(thread_id)
    if thread is None or thread.get("status") == ThreadStatus.CLOSED:
        return None
    if (
        resolve_bus_lifecycle(thread.get("tags"), explicit=explicit_bus_lifecycle)
        == "persistent"
    ):
        return None
    summary = f"Dispatch {terminal_status} — auto-closed (ephemeral default)."
    # Leave the closeout turn unread so wait()->fetch_unread consumers still
    # surface it; status=closed already halts further work on the thread.
    return close_thread(thread_id, summary=summary, mark_all_read=False)


def maybe_auto_close_after_implement_handoff_reply(
    thread_id: str,
    *,
    turn_number: int,
    from_agent: str,
) -> dict[str, Any] | None:
    if turn_number <= 1:
        return None
    thread = get_thread(thread_id)
    if thread is None or thread.get("status") == ThreadStatus.CLOSED:
        return None
    tags = thread.get("tags") or []
    if "contract:implement" not in tags:
        return None
    if resolve_bus_lifecycle(tags) == "persistent":
        return None
    turns = get_thread_turns_asc(thread_id)
    if not turns:
        return None
    implement_seat = turns[0].get("to_agent")
    if not implement_seat:
        return None
    if normalize_agent_slug(from_agent) != normalize_agent_slug(implement_seat):
        return None
    summary = f"Implement reply (turn {turn_number}) — auto-closed (ephemeral default)."
    return close_thread(thread_id, summary=summary, mark_all_read=False)
