"""Pure-function tests for handoff wait status derivation."""

from __future__ import annotations

from datetime import UTC, datetime

from chat_harvest.test_chrome import (
    SPECIMEN_346_BODY,
    SPECIMEN_347_BODY,
    SPECIMEN_12887_PROGRESS_BODY,
)

from agent_bus_store.producer_projection import classify_producer_link
from agent_bus_store.turns_models import ThreadStatus
from agent_bus_store.wait_status import (
    build_suggested_next,
    derive_status,
    is_complete,
    qualifying_proof_reply,
)


def _turn(n, frm, read_at=None, status="open", *, subject="", body=""):
    row = {"turn_number": n, "from_agent": frm, "read_at": read_at, "status": status}
    if subject:
        row["subject"] = subject
    if body:
        row["body"] = body
    return row


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


def test_status_ignores_read_at_when_no_later_turn():
    """C contract: no-reply status ignores pointer read_at; never awaiting_push."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "first_reply_from", "from_agent": "claude-web"}

    # No reply yet — and crucially, read_at on the pointer is IRRELEVANT to status.
    pending_unread = [_turn(1, "cursor", read_at=None)]
    pending_read = [_turn(1, "cursor", read_at="2026-06-03T12:00:00Z")]
    for turns in (pending_unread, pending_read):
        assert (
            derive_status(thread, turns, after_turn=1, completion=comp) == "no_new_turn"
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

    assert set(get_args(WaitStatus)) == {
        "no_new_turn",
        "predicate_unmet",
        "complete",
        "producer_terminal",
    }
    assert "awaiting_push" not in get_args(WaitStatus)


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


def test_proof_reply_from_specimen_346_predicate_unmet():
    """#346 — chrome-only CDP envelope must not complete proof_reply_from."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "proof_reply_from", "from_agent": "web-anthropic"}
    turns = [
        _turn(1, "cursor"),
        _turn(
            2,
            "web-anthropic",
            subject="cdp reply — a76a67d3",
            body=SPECIMEN_346_BODY,
        ),
    ]
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        qualifying_proof_reply(turns, after_turn=1, from_agent="web-anthropic") is None
    )
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp) == "predicate_unmet"
    )


def test_proof_reply_from_specimen_347_complete():
    """#347 — substantive prose after envelope completes proof_reply_from."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "proof_reply_from", "from_agent": "web-anthropic"}
    turns = [
        _turn(1, "cursor"),
        _turn(
            2,
            "web-anthropic",
            subject="cdp reply — b87b78e4",
            body=SPECIMEN_347_BODY,
        ),
    ]
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    reply = qualifying_proof_reply(turns, after_turn=1, from_agent="web-anthropic")
    assert reply is not None
    assert reply["turn_number"] == 2
    assert derive_status(thread, turns, after_turn=1, completion=comp) == "complete"


def test_proof_reply_skips_progress_turn_for_later_substantive():
    """A tool-badge turn is not the closeout; the next substantive reply is."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "proof_reply_from", "from_agent": "web-anthropic"}
    turns = [
        _turn(1, "cursor"),
        _turn(
            2,
            "web-anthropic",
            subject="cdp reply — 260e806d",
            body=SPECIMEN_12887_PROGRESS_BODY,
        ),
        _turn(
            3,
            "web-anthropic",
            subject="cdp reply — 260e806d",
            body=SPECIMEN_347_BODY,
        ),
    ]
    assert not is_complete(
        thread,
        turns[:2],
        after_turn=1,
        completion=comp,
    )
    reply = qualifying_proof_reply(turns, after_turn=1, from_agent="web-anthropic")
    assert reply is not None
    assert reply["turn_number"] == 3
    assert derive_status(thread, turns, after_turn=1, completion=comp) == "complete"


def test_proof_reply_from_failed_envelope_predicate_unmet():
    """FAILED CDP envelope subject never satisfies proof_reply_from."""
    thread = {"status": ThreadStatus.ACTIVE}
    comp = {"mode": "proof_reply_from", "from_agent": "web-anthropic"}
    turns = [
        _turn(1, "cursor"),
        _turn(
            2,
            "web-anthropic",
            subject="cdp FAILED — deadbeef",
            body=SPECIMEN_347_BODY,
        ),
    ]
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        derive_status(thread, turns, after_turn=1, completion=comp) == "predicate_unmet"
    )


def test_proof_reply_from_failed_link_producer_terminal():
    """Failed cdp-generate dispatch link yields producer_terminal when execution_id matches."""
    thread = {
        "status": ThreadStatus.ACTIVE,
        "dispatch_links": [
            {
                "execution_id": "exec-fail",
                "pipeline_id": "cdp-generate",
                "terminal_status": "failed",
            }
        ],
    }
    comp = {"mode": "proof_reply_from", "from_agent": "web-anthropic"}
    turns = [
        _turn(1, "cursor"),
        _turn(
            2,
            "web-anthropic",
            subject="cdp FAILED — deadbeef",
            body=SPECIMEN_347_BODY,
        ),
    ]
    assert not is_complete(thread, turns, after_turn=1, completion=comp)
    assert (
        derive_status(
            thread,
            turns,
            after_turn=1,
            completion=comp,
            execution_id="exec-fail",
        )
        == "producer_terminal"
    )


def test_non_cdp_generate_failed_link_stays_predicate_unmet():
    """SDK failed link must not satisfy producer_terminal (M4 guard)."""
    thread = {
        "status": ThreadStatus.ACTIVE,
        "dispatch_links": [
            {
                "execution_id": "exec-sdk",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": "failed",
            }
        ],
    }
    comp = {"mode": "proof_reply_from", "from_agent": "web-anthropic"}
    turns = [
        _turn(1, "cursor"),
        _turn(
            2,
            "web-anthropic",
            subject="cdp FAILED — deadbeef",
            body=SPECIMEN_347_BODY,
        ),
    ]
    assert (
        derive_status(
            thread,
            turns,
            after_turn=1,
            completion=comp,
            execution_id="exec-sdk",
        )
        == "predicate_unmet"
    )


def test_sdk_completed_without_proof_is_producer_terminal():
    """a:38447 — pinned completed link with no proof on waited thread fail-closes."""
    thread = {
        "status": ThreadStatus.ACTIVE,
        "dispatch_links": [
            {
                "execution_id": "883d2ea2",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": "completed",
            }
        ],
    }
    comp = {"mode": "proof_reply_from", "from_agent": "cursor-sdk"}
    # Specimen 15469: admit-only after worker CLOSEOUT completed elsewhere.
    turns = [_turn(1, "dispatch", subject="cursor-sdk generate admitted")]
    assert not is_complete(thread, turns, after_turn=0, completion=comp)
    assert (
        derive_status(
            thread,
            turns,
            after_turn=0,
            completion=comp,
            execution_id="883d2ea2",
        )
        == "producer_terminal"
    )
    assert (
        derive_status(
            thread,
            turns,
            after_turn=1,
            completion=comp,
            execution_id="883d2ea2",
        )
        == "producer_terminal"
    )


def test_classify_producer_link_unknown_without_execution_id() -> None:
    assert classify_producer_link(execution_id=None, dispatch_links=[]) == {
        "execution_id": None,
        "pipeline_id": None,
        "state": "unknown",
        "terminal_status": None,
        "linked_at": None,
        "delivery_at": None,
        "source": "thread_dispatch_links",
        "liveness_reason": "execution_omitted",
    }


def test_classify_producer_link_unlinked_when_row_missing() -> None:
    assert classify_producer_link(
        execution_id="exec-missing",
        dispatch_links=[
            {
                "execution_id": "exec-other",
                "pipeline_id": "cdp-generate",
                "linked_at": "2026-09-07T05:41:20Z",
                "terminal_status": None,
                "delivery_at": None,
            }
        ],
    ) == {
        "execution_id": "exec-missing",
        "pipeline_id": None,
        "state": "unlinked",
        "terminal_status": None,
        "linked_at": None,
        "delivery_at": None,
        "source": "thread_dispatch_links",
        "liveness_reason": "no_row",
    }


def test_classify_producer_link_in_flight_from_gate0_payload() -> None:
    """Gate-0 captured row stays in_flight only inside the admit grace."""
    row = {
        "execution_id": "d6a93d64-18a9-4779-8238-89d6af49e415",
        "pipeline_id": "cdp-generate",
        "linked_at": "2026-09-07T05:41:20Z",
        "terminal_status": None,
        "delivery_at": None,
    }
    assert classify_producer_link(
        execution_id="d6a93d64-18a9-4779-8238-89d6af49e415",
        dispatch_links=[row],
        now=datetime(2026, 9, 7, 5, 42, 0, tzinfo=UTC),
    ) == {
        "execution_id": "d6a93d64-18a9-4779-8238-89d6af49e415",
        "pipeline_id": "cdp-generate",
        "state": "in_flight",
        "terminal_status": None,
        "linked_at": "2026-09-07T05:41:20Z",
        "delivery_at": None,
        "source": "thread_dispatch_links",
        "liveness_reason": "admit_grace",
    }


def test_dead_stream_without_terminal_write_is_not_in_flight() -> None:
    """a:36832 specimen: stream died, link never terminalized.

    Seven days after admit the row is still terminal_status NULL and
    delivery_at NULL. The reader reports unknown, not in_flight, and does
    not write terminal_status. A live witness still reads in_flight. A dead
    witness reports stream_dead_no_terminal without a terminal write.
    a:36651 (live producer wrongly terminalized) stays the writer-side guard
    in test_terminal_execution_scope.py.
    """
    row = {
        "execution_id": "96f5f3f0-2867-477a-99e5-d79573d77d1e",
        "pipeline_id": "cdp-generate",
        "linked_at": "2026-09-22T02:44:02Z",
        "terminal_status": None,
        "delivery_at": None,
    }
    observed = datetime(2026, 9, 29, 6, 5, 58, tzinfo=UTC)
    stale = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
    )
    assert stale["state"] == "unknown"
    assert stale["liveness_reason"] == "no_liveness_signal"
    assert stale["terminal_status"] is None
    assert row["terminal_status"] is None

    dead = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
        liveness_witness="dead",
    )
    assert dead["state"] == "unknown"
    assert dead["liveness_reason"] == "stream_dead_no_terminal"
    assert row["terminal_status"] is None

    live = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
        liveness_witness="live",
    )
    assert live["state"] == "in_flight"
    assert live["liveness_reason"] == "witness_live"
    assert row["terminal_status"] is None


def _past_grace_row() -> dict:
    return {
        "execution_id": "67aae3ee-ff0b-4e63-bfad-de87ceac2f93",
        "pipeline_id": "cursor-sdk-generate",
        "linked_at": "2026-09-29T06:38:48Z",
        "terminal_status": None,
        "delivery_at": None,
    }


def test_reader_witness_skip_live_is_in_flight_without_terminal_write(
    monkeypatch,
) -> None:
    """Past grace, SKIP_LIVE reads in_flight / witness_live and does not terminalize."""
    from agent_bus_store.sdk_liveness import LivenessVerdict, reader_liveness_witness

    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        lambda **_kwargs: (LivenessVerdict.SKIP_LIVE, "worker_live", None),
    )
    row = _past_grace_row()
    observed = datetime(2026, 9, 29, 6, 44, tzinfo=UTC)
    witness = reader_liveness_witness(
        thread_id="12286",
        execution_id=row["execution_id"],
        linked_at=row["linked_at"],
        now=observed,
    )
    assert witness == "live"
    classified = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
        liveness_witness=witness,
    )
    assert classified["state"] == "in_flight"
    assert classified["liveness_reason"] == "witness_live"
    assert classified["terminal_status"] is None
    assert row["terminal_status"] is None


def test_reader_witness_heartbeat_stale_is_unknown_without_terminal_write(
    monkeypatch,
) -> None:
    """Past grace, heartbeat_stale reads unknown / stream_dead_no_terminal."""
    from agent_bus_store.sdk_liveness import LivenessVerdict, reader_liveness_witness

    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        lambda **_kwargs: (LivenessVerdict.ALLOW_ORPHAN, "heartbeat_stale", None),
    )
    row = _past_grace_row()
    observed = datetime(2026, 9, 29, 6, 44, tzinfo=UTC)
    witness = reader_liveness_witness(
        thread_id="12286",
        execution_id=row["execution_id"],
        linked_at=row["linked_at"],
        now=observed,
    )
    assert witness == "dead"
    classified = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
        liveness_witness=witness,
    )
    assert classified["state"] == "unknown"
    assert classified["liveness_reason"] == "stream_dead_no_terminal"
    assert classified["terminal_status"] is None
    assert row["terminal_status"] is None


def test_reader_witness_defer_stays_no_liveness_signal(monkeypatch) -> None:
    """Past grace, DEFER (probe error or timeout) stays unknown / no_liveness_signal."""
    from agent_bus_store.sdk_liveness import LivenessVerdict, reader_liveness_witness

    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        lambda **_kwargs: (LivenessVerdict.DEFER, "probe_unreachable:timeout", None),
    )
    row = _past_grace_row()
    observed = datetime(2026, 9, 29, 6, 44, tzinfo=UTC)
    witness = reader_liveness_witness(
        thread_id="12286",
        execution_id=row["execution_id"],
        linked_at=row["linked_at"],
        now=observed,
    )
    assert witness is None
    classified = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
        liveness_witness=witness,
    )
    assert classified["state"] == "unknown"
    assert classified["liveness_reason"] == "no_liveness_signal"
    assert row["terminal_status"] is None


def test_reader_witness_other_verdicts_do_not_map_to_live_or_dead(
    monkeypatch,
) -> None:
    """404, mismatch, and terminal backfill stay None. They are not death or life."""
    from agent_bus_store.sdk_liveness import LivenessVerdict, reader_liveness_witness

    observed = datetime(2026, 9, 29, 6, 44, tzinfo=UTC)
    cases = (
        (LivenessVerdict.ALLOW_ORPHAN, "probe_not_found", None),
        (LivenessVerdict.ALLOW_ORPHAN, "execution_id_mismatch", None),
        (LivenessVerdict.TERMINAL_BACKFILL, "probe_terminal", "failed"),
    )

    def _probe_for(verdict, reason, terminal):
        def _probe(**_kwargs):
            return verdict, reason, terminal

        return _probe

    for verdict, reason, terminal in cases:
        monkeypatch.setattr(
            "agent_bus_store.sdk_liveness.evaluate_link_liveness",
            _probe_for(verdict, reason, terminal),
        )
        witness = reader_liveness_witness(
            thread_id="12286",
            execution_id="67aae3ee-ff0b-4e63-bfad-de87ceac2f93",
            linked_at="2026-09-29T06:38:48Z",
            now=observed,
        )
        assert witness is None, reason


def test_reader_witness_inside_grace_does_not_probe(monkeypatch) -> None:
    """Inside the admit grace the probe is not called and classification stays admit_grace."""
    from agent_bus_store.sdk_liveness import reader_liveness_witness

    def _boom(**_kwargs):
        raise AssertionError("probe called inside grace")

    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        _boom,
    )
    observed = datetime(2026, 9, 29, 6, 39, 0, tzinfo=UTC)
    linked_at = "2026-09-29T06:38:48Z"
    row = {
        "execution_id": "67aae3ee-ff0b-4e63-bfad-de87ceac2f93",
        "pipeline_id": "cursor-sdk-generate",
        "linked_at": linked_at,
        "terminal_status": None,
        "delivery_at": None,
    }
    witness = reader_liveness_witness(
        thread_id="12286",
        execution_id=row["execution_id"],
        linked_at=linked_at,
        now=observed,
    )
    assert witness is None
    classified = classify_producer_link(
        execution_id=row["execution_id"],
        dispatch_links=[row],
        now=observed,
        liveness_witness=witness,
    )
    assert classified["state"] == "in_flight"
    assert classified["liveness_reason"] == "admit_grace"
    assert row["terminal_status"] is None


def test_cursor_sdk_park_and_resume_do_not_complete_wait() -> None:
    """Park and resume notices are not the dispatch reply the watcher waits for."""
    thread = {"status": ThreadStatus.ACTIVE}
    turns = [
        _turn(1, "dispatch"),
        _turn(
            2,
            "cursor-sdk",
            subject="cursor-sdk dispatch d1 PARKED (for GIW restart i)",
            body='{"status": "parked"}',
        ),
        _turn(
            3,
            "cursor-sdk",
            subject="cursor-sdk dispatch d1-r1 RESUMED (resume_of d1, restart i)",
            body='{"status": "resumed"}',
        ),
    ]
    for mode in ("first_reply_from", "proof_reply_from"):
        comp = {"mode": mode, "from_agent": "cursor-sdk"}
        assert not is_complete(thread, turns, after_turn=1, completion=comp)
        assert derive_status(thread, turns, after_turn=1, completion=comp) == (
            "predicate_unmet"
        )
    turns.append(
        _turn(
            4,
            "cursor-sdk",
            subject="closeout: agent-bus-write-ticket Part A",
            body="Part A landed.\nstatus: complete\n",
        )
    )
    comp = {"mode": "proof_reply_from", "from_agent": "cursor-sdk"}
    assert is_complete(thread, turns, after_turn=1, completion=comp)
    reply = qualifying_proof_reply(turns, after_turn=1, from_agent="cursor-sdk")
    assert reply is not None and reply["turn_number"] == 4


def test_classify_producer_link_terminal() -> None:
    assert (
        classify_producer_link(
            execution_id="exec-done",
            dispatch_links=[
                {
                    "execution_id": "exec-done",
                    "pipeline_id": "cdp-generate",
                    "linked_at": "2026-09-07T05:41:20Z",
                    "terminal_status": "failed",
                    "delivery_at": "2026-09-07T06:00:00Z",
                }
            ],
        )["state"]
        == "terminal"
    )
