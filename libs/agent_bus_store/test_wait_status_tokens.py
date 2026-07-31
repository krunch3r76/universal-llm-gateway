"""Tests for Auto terminal status: wait completion modes."""

from __future__ import annotations

from agent_bus_store.turns_models import ThreadStatus
from agent_bus_store.wait_status import (
    derive_status,
    is_complete,
    qualifying_status_turn,
)


def _turn(n, frm, subject="", body="", read_at=None, status="open"):
    return {
        "turn_number": n,
        "from_agent": frm,
        "subject": subject,
        "body": body,
        "read_at": read_at,
        "status": status,
    }


def test_status_done_incomplete_then_complete():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [_turn(1, "web-anthropic", subject="request")]
    comp = {"mode": "status:done"}
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    turns.append(_turn(2, "cursor-auto", subject="status:admitted — x"))
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    turns.append(_turn(3, "cursor-auto", subject="status:done — x"))
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    assert derive_status(thread, turns, after_turn=1, completion=comp) == "complete"


def test_status_needs_attended_in_body():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(1, "web-anthropic"),
        _turn(2, "cursor-auto", body="terminal status:needs-attended\n"),
    ]
    comp = {"mode": "status:needs-attended"}
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    q = qualifying_status_turn(
        turns, after_turn=1, status_token="status:needs-attended"
    )
    assert q is not None and q["turn_number"] == 2


def test_status_failed_does_not_match_done():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(1, "web-anthropic"),
        _turn(2, "cursor-auto", subject="status:failed — boom"),
    ]
    assert not is_complete(
        thread, turns, after_turn=1, completion={"mode": "status:done"}
    )
    assert is_complete(
        thread, turns, after_turn=1, completion={"mode": "status:failed"}
    )


def test_empty_snapshot_does_not_falsely_complete():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [_turn(1, "web-anthropic")]
    for mode in ("status:done", "status:failed", "status:needs-attended"):
        assert not is_complete(
            thread, turns, after_turn=1, completion={"mode": mode}
        )
        assert (
            derive_status(thread, turns, after_turn=1, completion={"mode": mode})
            == "awaiting_first_reply"
        )
