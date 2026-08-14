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
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp)
        == "no_new_turn"
    )
    turns.append(_turn(2, "cursor-auto", subject="status:admitted — x"))
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp)
        == "predicate_unmet"
    )
    turns.append(_turn(3, "cursor-auto", subject="status:done — x"))
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    assert derive_status(thread, turns, after_turn=1, completion=comp) == "complete"


def test_status_done_admit_turn_is_predicate_unmet_not_no_new_turn():
    """AC2: liveness read distinguishes turn_count-advanced from no-reply.

    Incident shape (thread 7197): completion=status:done over a live
    status:admitted turn must not say no_new_turn.
    """
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(54, "web-anthropic", subject="request"),
        _turn(55, "cursor-auto", subject="status:admitted — nested dispatch"),
    ]
    comp = {"mode": "status:done"}
    assert not is_complete(thread, turns, after_turn=54, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=54, completion=comp)
        == "predicate_unmet"
    )
    empty_after = [_turn(54, "web-anthropic", subject="request")]
    assert (
        derive_status(thread, empty_after, after_turn=54, completion=comp)
        == "no_new_turn"
    )


def test_first_reply_from_other_author_is_predicate_unmet():
    """A later turn from the wrong seat is not 'no reply exists'."""
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [_turn(1, "cursor"), _turn(2, "web-anthropic")]
    comp = {"mode": "first_reply_from", "from_agent": "cursor-auto"}
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp)
        == "predicate_unmet"
    )


def test_status_needs_attended_body_only_does_not_complete():
    """AC1: no live producer emits terminal tokens in body only — repointed from
    test_status_needs_attended_in_body, which asserted the old substring bug."""
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(1, "web-anthropic"),
        _turn(2, "cursor-auto", body="terminal status:needs-attended\n"),
    ]
    comp = {"mode": "status:needs-attended"}
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp)
        == "predicate_unmet"
    )
    assert (
        qualifying_status_turn(
            turns, after_turn=1, status_token="status:needs-attended"
        )
        is None
    )


# Live thread 7233 turn 18 — admit subject prose mentions status:done (trailing space).
_THREAD_7233_TURN_18_SUBJECT = (
    "status:admitted — G1 — the hop verb's own contract says it never reports "
    "status:done; the cadence "
)


def test_status_done_substring_in_admit_subject_is_not_terminal():
    """Defends against admit-turn prose falsely completing status:done waiters."""
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(17, "web-anthropic", subject="request"),
        _turn(18, "cursor-auto", subject=_THREAD_7233_TURN_18_SUBJECT),
    ]
    comp = {"mode": "status:done"}
    assert not is_complete(thread, turns, after_turn=17, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=17, completion=comp)
        == "predicate_unmet"
    )
    assert (
        qualifying_status_turn(turns, after_turn=17, status_token="status:done")
        is None
    )


def test_status_done_in_body_prose_only_is_not_terminal():
    """Defends against CLOSEOUT-adjacent body prose completing status:done wait."""
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(1, "web-anthropic", subject="request"),
        _turn(
            2,
            "cursor-auto",
            subject="progress — nested dispatch",
            body="Poll with completion=status:done on the returned poll_hint.\n",
        ),
    ]
    comp = {"mode": "status:done"}
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp)
        == "predicate_unmet"
    )


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


def test_status_superseded_completes_wait():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(1, "web-anthropic", subject="request"),
        _turn(2, "cursor-auto", subject="status:superseded — withdrawn"),
    ]
    comp = {"mode": "status:superseded"}
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    assert derive_status(thread, turns, after_turn=1, completion=comp) == "complete"
    q = qualifying_status_turn(
        turns, after_turn=1, status_token="status:superseded"
    )
    assert q is not None and q["turn_number"] == 2


def test_empty_snapshot_does_not_falsely_complete():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [_turn(1, "web-anthropic")]
    for mode in (
        "status:done",
        "status:failed",
        "status:needs-attended",
        "status:superseded",
    ):
        assert not is_complete(
            thread, turns, after_turn=1, completion={"mode": mode}
        )
        assert (
            derive_status(thread, turns, after_turn=1, completion={"mode": mode})
            == "no_new_turn"
        )
