"""Bus-thread disposition — ephemeral auto-close by default."""

from __future__ import annotations

import re
from typing import Any, Literal

from agent_seat.registry import normalize_bus_address

from .db.threads import get_thread, get_thread_turns_asc
from .db.threads_atomic import close_thread
from .turns_models import ThreadStatus

BusLifecycle = Literal["persistent", "ephemeral"]
_LIFECYCLE_EPHEMERAL = "bus_lifecycle:ephemeral"
_LIFECYCLE_PERSISTENT = "bus_lifecycle:persistent"
_DONE_PREFIX = "DONE — "
# Machine close one-liners that must never become standing so-what titles
# (pager_notify / bus_scan prefix SMS with ThreadDetail.summary).
_MACHINE_CLOSE_MARKERS = (
    "auto-closed (ephemeral default)",
    "auto-closed (close-on-read)",
)
_DONE_STRIP_RE = re.compile(r"(?i)^DONE\s*[—\-:]?\s*")


def is_machine_close_summary(summary: str | None) -> bool:
    """True when summary is (or wraps) an auto-close machine one-liner."""
    text = (summary or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in _MACHINE_CLOSE_MARKERS)


def summary_for_auto_close(prior: str | None) -> str | None:
    """Preserve standing so-what on auto-close; never write machine junk alone.

    Returns ``None`` so ``close_thread`` leaves the DB ``summary`` column
    unchanged (it only writes when the arg is not None). Real so-what titles
    become ``DONE — {so_what}``.
    """
    prior = (prior or "").strip()
    if not prior or is_machine_close_summary(prior):
        return None
    if prior.startswith(_DONE_PREFIX):
        return prior
    cleaned = _DONE_STRIP_RE.sub("", prior).strip()
    if not cleaned or is_machine_close_summary(cleaned):
        return None
    return f"{_DONE_PREFIX}{cleaned}"


def _dispatch_base_seat(agent: str) -> str:
    """Strip per-dispatch scope suffix (``cursor-sdk:dispatch:{uuid}`` → ``cursor-sdk``)."""
    if ":dispatch:" in agent:
        return agent.split(":dispatch:", 1)[0]
    return agent


def agents_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_base = _dispatch_base_seat(str(left))
    right_base = _dispatch_base_seat(str(right))
    return normalize_bus_address(left_base) == normalize_bus_address(right_base)


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


def _has_delivered_result_turn(turns: list[dict[str, Any]]) -> bool:
    """True when a dispatch closeout turn exists after the pointer (turn >= 2).

    Only ``cursor-sdk`` (base seat) closeouts count — bare ``cursor`` /
    ``cursor-auto`` notes must not satisfy the pointer geometry alone.
    """
    if len(turns) < 2:
        return False
    pointer = turns[0]
    role = pointer.get("to_agent")
    caller = pointer.get("from_agent")
    if not role or not caller:
        return False
    expected_role = str(role)
    expected_caller = str(caller)
    for turn in turns[1:]:
        if int(turn.get("turn_number") or 0) < 2:
            continue
        from_agent = str(turn.get("from_agent") or "")
        if normalize_bus_address(_dispatch_base_seat(from_agent)) != "cursor-sdk":
            continue
        if agents_match(from_agent, expected_role) and agents_match(
            str(turn.get("to_agent")), expected_caller
        ):
            return True
    return False


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
    tags = thread.get("tags") or []
    if "lane:cursor-auto" in tags:
        return None
    if resolve_bus_lifecycle(tags, explicit=explicit_bus_lifecycle) == "persistent":
        return None
    turns = get_thread_turns_asc(thread_id)
    if not _has_delivered_result_turn(turns):
        return None
    # Preserve standing so-what; ¬ wipe with machine one-liner (pager reads summary).
    summary = summary_for_auto_close(thread.get("summary"))
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
    if normalize_bus_address(from_agent) != normalize_bus_address(implement_seat):
        return None
    summary = summary_for_auto_close(thread.get("summary"))
    return close_thread(thread_id, summary=summary, mark_all_read=False)
