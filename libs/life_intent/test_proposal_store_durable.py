"""Restart-durable SQLite proposal store — Gate-6 decisive falsifier."""

from __future__ import annotations

import threading

from life_intent.proposal_store import (
    begin_apply,
    clear_store,
    create_proposal,
    get_proposal,
    mark_completed,
    process_epoch,
    record_dispatch_handle,
    reset_connection_for_tests,
)


def test_restart_reclaims_stale_applying_with_same_handle() -> None:
    proposal_id = create_proposal(
        normalized_intent={
            "verb": "investigate",
            "subject": "restart durable",
            "detail": "Proposal must survive stargate process restart simulation.",
        },
        work_order="scout",
        verb="investigate",
        lane="recon",
    )
    handle = {
        "request_id": "req-1",
        "execution_id": "exec-1",
        "dispatch_id": "req-1-aa",
        "thread_id": "agent-bus:life-intent-restart-durable",
    }
    first, code = begin_apply(proposal_id)
    assert code is None and first is not None
    record_dispatch_handle(proposal_id, handle, reply_thread=handle["thread_id"])
    owner_before = process_epoch()
    row = get_proposal(proposal_id)
    assert row is not None
    assert row.status == "applying"
    assert row.apply_owner == owner_before
    assert row.dispatch_handle == handle

    reset_connection_for_tests()
    assert process_epoch() != owner_before

    results: list[tuple] = []

    def _race() -> None:
        results.append(begin_apply(proposal_id))

    t1 = threading.Thread(target=_race)
    t2 = threading.Thread(target=_race)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winners = [r for r in results if r[1] is None]
    losers = [r for r in results if r[1] == "proposal_already_committed"]
    assert len(winners) == 1
    assert len(losers) == 1
    winner_row = winners[0][0]
    assert winner_row is not None
    assert winner_row.dispatch_handle == handle
    assert winner_row.apply_owner == process_epoch()

    mark_completed(proposal_id)
    reset_connection_for_tests()
    terminal = get_proposal(proposal_id)
    assert terminal is not None
    assert terminal.status == "completed"
    assert terminal.dispatch_handle == handle


def test_same_epoch_concurrent_apply_rejects() -> None:
    clear_store()
    proposal_id = create_proposal(
        normalized_intent={
            "verb": "investigate",
            "subject": "same epoch",
            "detail": "Concurrent apply in one process must reject the loser.",
        },
        work_order="scout",
        verb="investigate",
        lane="recon",
    )
    first, code = begin_apply(proposal_id)
    assert code is None and first is not None
    second, code2 = begin_apply(proposal_id)
    assert second is None
    assert code2 == "proposal_already_committed"
