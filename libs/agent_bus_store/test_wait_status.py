"""Pure-function tests for handoff wait status derivation."""

from __future__ import annotations

from agent_bus_store.turns_models import ThreadStatus
from agent_bus_store.wait_status import build_suggested_next, derive_status, is_complete


def _turn(n, frm, read_at=None, status="open"):
    return {"turn_number": n, "from_agent": frm, "read_at": read_at, "status": status}


def test_first_reply_from_incomplete_then_complete():
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [_turn(1, "cursor")]
    comp = {"mode": "first_reply_from", "from_agent": "claude-web"}
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    turns.append(_turn(2, "claude-web"))
    assert is_complete(thread, turns, after_turn=1, completion=comp)


def test_first_reply_from_matches_new_old_bus_address():
    """Address layer: claude-web hint matches web-anthropic reply and reverse."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "first_reply_from", "from_agent": "claude-web"}
    turns = [_turn(1, "dispatch"), _turn(2, "web-anthropic")]
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    comp_rev = {"mode": "first_reply_from", "from_agent": "web-anthropic"}
    turns_rev = [_turn(1, "dispatch"), _turn(2, "claude-web")]
    assert is_complete(thread, turns_rev, after_turn=1, completion=comp_rev)


def test_first_reply_from_matches_retired_cdp_seat_alias():
    """Retired bus seat ``cdp`` aliases to endpoint ``web-anthropic``."""
    thread = {"status": ThreadStatus.ACTIVE}
    # Legacy poll_hint from_agent=cdp matches product/on-behalf from=web-anthropic
    comp_legacy = {"mode": "first_reply_from", "from_agent": "cdp"}
    turns = [_turn(1, "cursor"), _turn(2, "web-anthropic")]
    assert is_complete(thread, turns, after_turn=1, completion=comp_legacy)
    # Canonical poll_hint matches legacy on-behalf from=cdp still in flight
    comp = {"mode": "first_reply_from", "from_agent": "web-anthropic"}
    turns_legacy_from = [_turn(1, "cursor"), _turn(2, "cdp")]
    assert is_complete(thread, turns_legacy_from, after_turn=1, completion=comp)


def test_first_reply_from_matches_legacy_alias():
    """thread-1248 regression: hint names canonical seat, reply posts under alias."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "first_reply_from", "from_agent": "claude-cursor"}
    turns = [_turn(1, "cursor")]
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    turns.append(_turn(2, "cursor"))
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    comp_legacy = {"mode": "first_reply_from", "from_agent": "cursor"}
    turns_canon = [_turn(1, "cursor"), _turn(2, "claude-cursor")]
    assert is_complete(thread, turns_canon, after_turn=1, completion=comp_legacy)


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
    assert derive_status(thread, replied, after_turn=1, completion=comp) == "complete"


def test_suggested_next_names_consult_turn_not_pointer():
    thread = {"status": ThreadStatus.ACTIVE, "tags": ["bus_lifecycle:persistent"]}
    comp = {"mode": "first_reply_from", "from_agent": "claude-cursor"}
    turns = [_turn(1, "cursor"), _turn(2, "claude-cursor")]
    nudge = build_suggested_next(
        thread,
        complete=True,
        completion=comp,
        qualifying_reply_turn=2,
        after_turn=1,
        turns=turns,
    )
    assert nudge is not None
    assert nudge["consult_turn"] == 2
    assert nudge["pointer_turn"] == 1
    assert "turn 1 was the packet pointer" in nudge["message"]
    assert any(s["action"] == "close_handoff_thread" for s in nudge["steps"])


def test_suggested_next_silent_for_ephemeral():
    thread = {
        "status": ThreadStatus.ACTIVE,
        "tags": ["bus_lifecycle:ephemeral", "type:generate"],
    }
    comp = {"mode": "first_reply_from", "from_agent": "cursor-sdk"}
    turns = [_turn(1, "dispatch"), _turn(2, "cursor-sdk")]
    assert (
        build_suggested_next(
            thread,
            complete=True,
            completion=comp,
            qualifying_reply_turn=2,
            after_turn=1,
            turns=turns,
        )
        is None
    )


def test_suggested_next_mark_read_for_persistent_close_on_read():
    from agent_bus_store.close_on_read import CLOSE_ON_READ_TAG

    thread = {
        "status": ThreadStatus.ACTIVE,
        "tags": [
            "bus_lifecycle:persistent",
            "type:generate",
            CLOSE_ON_READ_TAG,
        ],
    }
    comp = {"mode": "first_reply_from", "from_agent": "cursor-sdk"}
    turns = [_turn(1, "dispatch"), _turn(2, "cursor-sdk", read_at=None)]
    nudge = build_suggested_next(
        thread,
        complete=True,
        completion=comp,
        qualifying_reply_turn=2,
        after_turn=1,
        turns=turns,
    )
    assert nudge is not None
    assert any(s["action"] == "mark_result_read" for s in nudge["steps"])
    assert not any(s["action"] == "close_handoff_thread" for s in nudge["steps"])


def test_suggested_next_silent_after_close_on_read_result_consumed():
    from agent_bus_store.close_on_read import CLOSE_ON_READ_TAG

    thread = {
        "status": ThreadStatus.ACTIVE,
        "tags": [
            "bus_lifecycle:persistent",
            "type:generate",
            CLOSE_ON_READ_TAG,
        ],
    }
    comp = {"mode": "first_reply_from", "from_agent": "cursor-sdk"}
    turns = [
        _turn(1, "dispatch"),
        _turn(2, "cursor-sdk", read_at="2026-06-03T12:00:00Z"),
    ]
    assert (
        build_suggested_next(
            thread,
            complete=True,
            completion=comp,
            qualifying_reply_turn=2,
            after_turn=1,
            turns=turns,
        )
        is None
    )


def test_cursor_sdk_reply_seat_matches_poll_hint_not_scoped_recipient():
    """T2: poll_hint names family seat; scoped dispatch address is not a reply author."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "first_reply_from", "from_agent": "cursor-sdk"}
    turns = [_turn(1, "dispatch"), _turn(2, "cursor-sdk")]
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    scoped = [_turn(1, "dispatch"), _turn(2, "cursor-sdk:dispatch:exec-1")]
    assert not is_complete(thread, scoped, after_turn=1, completion=comp)


def test_no_awaiting_push_status_exists():
    """Regression guard: the read_at-derived push states must NOT be emittable."""
    from typing import get_args

    from agent_bus_store.wait_status import WaitStatus

    assert set(get_args(WaitStatus)) == {"awaiting_first_reply", "complete"}


def _disposition_turn(n: int, *, verdict: str) -> dict:
    return {
        "turn_number": n,
        "from_agent": "web-anthropic",
        "body": f"TYPE: DISPOSITION\nverdict: {verdict}\n\n## notes\n...",
        "read_at": None,
        "status": "open",
    }


def test_dead_wait_one_correction_waiting_for_cursor():
    from agent_bus_store.wait_status import is_dead_wait_no_auto_producer

    turns = [_disposition_turn(20, verdict="one correction")]
    comp = {"mode": "first_reply_from", "from_agent": "cursor"}
    assert is_dead_wait_no_auto_producer(turns, after_turn=20, completion=comp)
    # alias that normalizes to cursor
    assert is_dead_wait_no_auto_producer(
        turns,
        after_turn=20,
        completion={"mode": "first_reply_from", "from_agent": "claude-cursor"},
    )


def test_dead_wait_not_for_ratify_or_status_done_or_cursor_auto():
    from agent_bus_store.wait_status import is_dead_wait_no_auto_producer

    turns = [_disposition_turn(20, verdict="ratify")]
    assert not is_dead_wait_no_auto_producer(
        turns,
        after_turn=20,
        completion={"mode": "first_reply_from", "from_agent": "cursor"},
    )
    one = [_disposition_turn(20, verdict="one correction")]
    assert not is_dead_wait_no_auto_producer(
        one,
        after_turn=20,
        completion={"mode": "status:done"},
    )
    assert not is_dead_wait_no_auto_producer(
        one,
        after_turn=20,
        completion={"mode": "first_reply_from", "from_agent": "cursor-auto"},
    )


def test_dead_wait_clears_when_cursor_already_replied():
    from agent_bus_store.wait_status import is_dead_wait_no_auto_producer

    turns = [
        _disposition_turn(20, verdict="one correction"),
        {
            "turn_number": 21,
            "from_agent": "cursor",
            "body": "TYPE: CLOSEOUT\nstatus: complete\n",
            "read_at": None,
            "status": "open",
        },
    ]
    assert not is_dead_wait_no_auto_producer(
        turns,
        after_turn=20,
        completion={"mode": "first_reply_from", "from_agent": "cursor"},
    )
