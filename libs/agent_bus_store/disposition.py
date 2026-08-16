"""Bus-thread disposition — ephemeral auto-close by default."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from agent_seat.registry import normalize_bus_address

from .db.threads import get_thread, get_thread_turns_asc
from .db.threads_atomic import close_thread
from .turns_models import ThreadStatus

BusLifecycle = Literal["persistent", "ephemeral"]

# Matches ``TYPE: DISPOSITION``, ``TYPE: LEG DISPOSITION …``, and
# ``TYPE: DISPOSITION+DIRECTIVE`` — not bare ``TYPE: DIRECTIVE``.
_DISPOSITION_TURN_TYPE_RE = re.compile(
    r"^TYPE:\s*(?:LEG\s+)?DISPOSITION(?:\+[\w-]+|\s|\(|$)",
    re.IGNORECASE,
)


def first_line_is_disposition_type(first_line: str) -> bool:
    """True when the first nonblank line is a disposition-family turn type."""
    line = (first_line or "").strip()
    if not line:
        return False
    return _DISPOSITION_TURN_TYPE_RE.match(line) is not None


def body_has_disposition_type(body: str) -> bool:
    """True when any line opens with a disposition-family ``TYPE:`` token."""
    for raw in (body or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if first_line_is_disposition_type(stripped):
            return True
        if stripped.upper().startswith("TYPE:"):
            return False
    return False
_LIFECYCLE_EPHEMERAL = "bus_lifecycle:ephemeral"
_LIFECYCLE_PERSISTENT = "bus_lifecycle:persistent"
_DONE_PREFIX = "DONE — "
_LAND_OWE_PREFIX = "LAND OWED — "
_LAND_REQUIRED_TAG = "land_required"
# Machine close one-liners that must never become standing so-what titles
# (pager_notify / bus_scan prefix SMS with ThreadDetail.summary).
_MACHINE_CLOSE_MARKERS = (
    "auto-closed (ephemeral default)",
    "auto-closed (close-on-read)",
)
_DONE_STRIP_RE = re.compile(r"(?i)^DONE\s*[—\-:]?\s*")
_LAND_OWE_STRIP_RE = re.compile(r"(?i)^LAND\s+OWED\s*[—\-:]?\s*")


def is_machine_close_summary(summary: str | None) -> bool:
    """True when summary is (or wraps) an auto-close machine one-liner."""
    text = (summary or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in _MACHINE_CLOSE_MARKERS)


def land_owed_for_summary(
    *,
    tags: list[str] | None = None,
    landed: bool | None = None,
    commits_ahead: int | None = None,
) -> bool:
    """True when auto-close must prefix ``LAND OWED —`` instead of ``DONE —``."""
    if tags and _LAND_REQUIRED_TAG in tags:
        return True
    return landed is False and (commits_ahead or 0) >= 1


def _compose_auto_close_prefix(*, land_owed: bool) -> str:
    return _LAND_OWE_PREFIX if land_owed else _DONE_PREFIX


def _strip_auto_close_prefix(text: str) -> str:
    cleaned = _LAND_OWE_STRIP_RE.sub("", text).strip()
    return _DONE_STRIP_RE.sub("", cleaned).strip()


def _closeout_land_meter_from_turn(body: str | None) -> tuple[bool | None, int | None]:
    """Best-effort parse of structured closeout ``landed`` / ``commits_ahead``."""
    raw = (body or "").strip()
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    landed = payload.get("landed")
    commits_raw = payload.get("commits_ahead")
    commits_ahead: int | None
    if commits_raw is None:
        commits_ahead = None
    else:
        try:
            commits_ahead = int(commits_raw)
        except (TypeError, ValueError):
            commits_ahead = None
    landed_bool = landed if isinstance(landed, bool) else None
    return landed_bool, commits_ahead


def _closeout_land_meter_from_turns(
    turns: list[dict[str, Any]],
) -> tuple[bool | None, int | None]:
    """Read land meter from the latest cursor-sdk closeout turn, if any."""
    for turn in reversed(turns):
        from_agent = str(turn.get("from_agent") or "")
        if normalize_bus_address(_dispatch_base_seat(from_agent)) != "cursor-sdk":
            continue
        if int(turn.get("turn_number") or 0) < 2:
            continue
        landed, commits_ahead = _closeout_land_meter_from_turn(
            str(turn.get("body") or "")
        )
        if landed is not None or commits_ahead is not None:
            return landed, commits_ahead
    return None, None


def summary_for_auto_close(
    prior: str | None,
    *,
    tags: list[str] | None = None,
    landed: bool | None = None,
    commits_ahead: int | None = None,
) -> str | None:
    """Preserve standing so-what on auto-close; never write machine junk alone.

    Returns ``None`` so ``close_thread`` leaves the DB ``summary`` column
    unchanged (it only writes when the arg is not None). Real so-what titles
    become ``DONE — {so_what}``, or ``LAND OWED — {so_what}`` when branch debt
    is open or the closeout reports ``landed=false`` with ``commits_ahead>=1``.
    """
    prior = (prior or "").strip()
    if not prior or is_machine_close_summary(prior):
        return None
    land_owed = land_owed_for_summary(
        tags=tags,
        landed=landed,
        commits_ahead=commits_ahead,
    )
    prefix = _compose_auto_close_prefix(land_owed=land_owed)
    if prior.startswith(prefix):
        return prior
    if not land_owed and prior.startswith(_DONE_PREFIX):
        return prior
    if land_owed and prior.startswith(_LAND_OWE_PREFIX):
        return prior
    cleaned = _strip_auto_close_prefix(prior)
    if not cleaned or is_machine_close_summary(cleaned):
        return None
    return f"{prefix}{cleaned}"


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
    landed, commits_ahead = _closeout_land_meter_from_turns(turns)
    summary = summary_for_auto_close(
        thread.get("summary"),
        tags=tags,
        landed=landed,
        commits_ahead=commits_ahead,
    )
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
    landed, commits_ahead = _closeout_land_meter_from_turns(turns)
    summary = summary_for_auto_close(
        thread.get("summary"),
        tags=tags,
        landed=landed,
        commits_ahead=commits_ahead,
    )
    return close_thread(thread_id, summary=summary, mark_all_read=False)
