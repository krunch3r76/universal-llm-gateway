"""Pure-function tests for handoff wait status derivation."""

from __future__ import annotations

from agent_bus_store.turns_models import ThreadStatus
from agent_bus_store.wait_status import derive_status, is_complete


def _turn(n, frm, read_at=None, status="open"):
    return {"turn_number": n, "from_agent": frm, "read_at": read_at, "status": status}


def test_first_reply_from_incomplete_then_complete():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [_turn(1, "cursor")]
    comp = {"mode": "first_reply_from", "from_agent": "claude-web"}
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    turns.append(_turn(2, "claude-web"))
    assert is_complete(thread, turns, after_turn=1, completion=comp)


def test_thread_closed_uses_thread_status_not_turn_status():
    """LANDMINE: closed thread with a lingering OPEN turn still satisfies."""
    thread = {"status": ThreadStatus.CLOSED}
    turns = [_turn(1, "cursor", status="open")]  # turn status open, thread closed
    comp = {"mode": "thread_closed"}
    assert is_complete(thread, turns, after_turn=1, completion=comp)


def test_status_is_two_state_only():
    """C contract: pre-reply is always awaiting_first_reply; never awaiting_push."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "first_reply_from", "from_agent": "claude-web"}

    # No reply yet — and crucially, read_at on the pointer is IRRELEVANT to status.
    pending_unread = [_turn(1, "cursor", read_at=None)]
    pending_read = [_turn(1, "cursor", read_at="2026-06-03T12:00:00Z")]
    for turns in (pending_unread, pending_read):
        assert (
            derive_status(thread, turns, after_turn=1, completion=comp)
            == "awaiting_first_reply"
        )

    # Qualifying reply lands → complete.
    replied = [_turn(1, "cursor"), _turn(2, "claude-web")]
    assert (
        derive_status(thread, replied, after_turn=1, completion=comp) == "complete"
    )


def test_no_awaiting_push_status_exists():
    """Regression guard: the read_at-derived push states must NOT be emittable."""
    from typing import get_args

    from agent_bus_store.wait_status import WaitStatus

    assert set(get_args(WaitStatus)) == {"awaiting_first_reply", "complete"}
