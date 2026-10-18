"""Sidebar title and wake gate for a warm liaison chat.

Callers are the bridge watcher and the debug-port driver. The open paste
asks the agent to rename the chat; this module decides whether a later wake
may paste. A bus ``TAB_READY`` does not satisfy the gate. The driver must
observe the active sidebar title and get an exact match first.

The wake line stays ``BRIDGE_WAKE thread=<id> turn=<n>`` so an existing
liaison prompt still recognizes it. The message body stays on the agent bus.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def chat_title(
    thread: str = "",
    slug: str = "",
    *,
    title: str | None = None,
) -> str:
    """Sidebar title both the opener and the selector use.

    When ``title`` is provided and non-empty, that exact single-line string
    is returned (for example a Grok Bot sidebar name like ``Grok Coordinator 1``).
    Otherwise ``thread`` and ``slug`` are stripped and joined as
    ``"<thread> <slug>"``. Either one empty, or a newline in any input,
    raises ``ValueError`` so a wake cannot target a blank or multi-line row.
    No I/O.
    """
    if title is not None and str(title).strip():
        return _one_line(str(title), "title")
    thread_s = _one_line(thread, "thread")
    slug_s = _one_line(slug, "slug")
    return f"{thread_s} {slug_s}"


def normalize_agent_id(agent_id: str | None) -> str:
    """Normalize an optional surface agent UUID/id (single line, stripped)."""
    if agent_id is None or not str(agent_id).strip():
        return ""
    return _one_line(str(agent_id), "agent_id")


def name_instruction(title: str) -> str:
    """First-paste sentence that creates the title selection will look up.

    The agent renames the chat to ``title`` and posts ``TAB_READY`` only
    after that rename. Cursor's open template already carries a longer form
    of this sentence; Grok Bot and any new surface use this one. No I/O.
    """
    exact = _one_line(title, "title")
    return (
        f"Name this chat exactly {exact!r}. "
        "Post TAB_READY only after the sidebar shows that title. "
        "Do not post TAB_READY before the rename."
    )


def wake_line(thread: str, turn: int | str | Sequence[int]) -> str:
    """Fixed paste for one or more bus turns. The turn body is not included.

    ``turn`` may be a positive int, a comma-separated string of positive ints,
    or a non-empty sequence of positive ints (sorted and deduplicated).
    The thread id is a single line. No I/O.
    """
    thread_s = _one_line(thread, "thread")
    turn_s = _format_wake_turns(turn)
    return f"BRIDGE_WAKE thread={thread_s} turn={turn_s}"


def _format_wake_turns(turn: int | str | Sequence[int]) -> str:
    if isinstance(turn, bool):
        raise ValueError("turn must be a positive int or non-empty turn list")
    if isinstance(turn, int):
        if turn < 1:
            raise ValueError("turn must be a positive int")
        return str(turn)
    if isinstance(turn, str):
        parts = [p.strip() for p in turn.split(",") if p.strip()]
        if not parts or not all(p.isdigit() and int(p) >= 1 for p in parts):
            raise ValueError("turn must be a positive int or comma-separated positive ints")
        numbers = sorted({int(p) for p in parts})
        return ",".join(str(n) for n in numbers)
    if isinstance(turn, Sequence):
        numbers: list[int] = []
        for item in turn:
            if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                raise ValueError("turn sequence must contain positive ints")
            numbers.append(item)
        if not numbers:
            raise ValueError("turn sequence must be non-empty")
        return ",".join(str(n) for n in sorted(set(numbers)))
    raise ValueError("turn must be a positive int or non-empty turn list")


def selection_verdict(active_title: str | None, expected_title: str) -> dict[str, Any]:
    """Whether the driver may paste a wake into the focused chat.

    Exact string match against the active sidebar title. A prefix, a window
    title such as ``Cursor Agents``, or a missing row is not a match.
    Returns ``ok``, ``reason``, ``active_title``, and ``expected_title``.
    Reasons are ``match``, ``title_mismatch``, ``no_active_chat``, and
    ``empty_expected``. No I/O.
    """
    expected = (expected_title or "").strip()
    active = (active_title or "").strip()
    if not expected:
        reason = "empty_expected"
        ok = False
    elif not active:
        reason = "no_active_chat"
        ok = False
    elif active != expected:
        reason = "title_mismatch"
        ok = False
    else:
        reason = "match"
        ok = True
    return {
        "ok": ok,
        "reason": reason,
        "active_title": active,
        "expected_title": expected,
    }


def _one_line(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValueError(f"{label} must be a single non-empty line")
    return text
