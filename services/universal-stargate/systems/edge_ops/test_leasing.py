"""Edge-facing behaviour: lease, started, result, lease loss, redelivery.

``NEVER_KIND`` is the redelivery=never case and ``ping`` the safe one. They
differ in exactly one place — whether a result may be posted before Stargate
has acknowledged ``started`` — and behave identically under lease loss, which
the parametrized cases below assert.
"""

from __future__ import annotations

import asyncio

import pytest

from systems.edge_ops import admission, leasing
from systems.edge_ops.model import (
    CODE_LEASE_MISMATCH,
    CODE_NOT_STARTED,
    CODE_TERMINAL,
    EdgeOpRefusedError,
)
from systems.edge_ops.store import EdgeOpsJournal

from .conftest import LEASE_TTL_SECONDS, NEVER_KIND, ManualClock, seen_edge


async def _queued(
    journal: EdgeOpsJournal,
    *,
    kind: str = "ping",
    key: str = "k1",
    deadline_s: float = 300.0,
) -> str:
    """Admit one op with an edge already present for its kind."""
    await seen_edge(journal, kinds=(kind,))
    return await admission.admit(
        journal,
        kind=kind,
        op=kind,
        args={"nonce": key},
        idempotency_key=key,
        deadline_s=deadline_s,
    )


@pytest.mark.asyncio
async def test_lease_started_result_round_trip(journal: EdgeOpsJournal) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is not None
    assert grant.op_id == op_id
    assert grant.redelivery == "safe"

    started = await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    assert started["status"] == "started"

    closed = await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": True, "pong": True},
    )
    assert closed["status"] == "completed"

    record = await journal.get(op_id)
    assert record is not None
    assert record.result == {"ok": True, "pong": True}


@pytest.mark.asyncio
async def test_lease_answers_nothing_when_the_queue_is_empty(
    journal: EdgeOpsJournal,
) -> None:
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is None


@pytest.mark.asyncio
async def test_lease_ignores_kinds_this_edge_does_not_serve(
    journal: EdgeOpsJournal,
) -> None:
    await _queued(journal, kind=NEVER_KIND, key="k9")
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is None


@pytest.mark.asyncio
async def test_two_edges_cannot_hold_the_same_op(journal: EdgeOpsJournal) -> None:
    await _queued(journal)
    first = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    second = await leasing.lease(journal, edge_id="edge-b", kinds=("ping",), wait_s=0.0)
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_ok_false_result_is_stored_verbatim_and_counts_as_completed(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    result = {"ok": False, "code": "no_composer", "detail": {"display": ":9"}}
    closed = await leasing.post_result(
        journal, op_id=op_id, edge_id="edge-a", lease_id=grant.lease_id, result=result
    )
    assert closed["status"] == "completed"
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "completed"
    assert record.result == result


@pytest.mark.asyncio
async def test_failed_outcome_is_separate_from_the_handler_result(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    closed = await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": False, "code": "edge_handler_exception"},
        outcome="failed",
    )
    assert closed["status"] == "failed"


@pytest.mark.asyncio
async def test_a_completed_op_is_never_reopened(journal: EdgeOpsJournal) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": True},
    )
    again = await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": False},
    )
    assert again["status"] == "completed"
    record = await journal.get(op_id)
    assert record is not None
    assert record.result == {"ok": True}


@pytest.mark.asyncio
async def test_a_write_from_another_edge_is_refused(journal: EdgeOpsJournal) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.post_event(
            journal,
            op_id=op_id,
            edge_id="edge-b",
            lease_id=grant.lease_id,
            event_type="started",
        )
    assert refusal.value.code == CODE_LEASE_MISMATCH


@pytest.mark.asyncio
async def test_each_event_renews_the_lease(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    clock.advance(LEASE_TTL_SECONDS - 1.0)
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="progress",
    )
    clock.advance(LEASE_TTL_SECONDS - 1.0)
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "leased"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ping", NEVER_KIND])
async def test_lease_lost_before_started_requeues(
    journal: EdgeOpsJournal,
    clock: ManualClock,
    kind: str,
) -> None:
    op_id = await _queued(journal, kind=kind, key=f"k-{kind}")
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=(kind,), wait_s=0.0)
    assert grant is not None
    clock.advance(LEASE_TTL_SECONDS + 1.0)

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "queued"
    assert record.leased_by is None

    regrant = await leasing.lease(journal, edge_id="edge-b", kinds=(kind,), wait_s=0.0)
    assert regrant is not None
    assert regrant.op_id == op_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ping", NEVER_KIND])
async def test_lease_lost_after_started_ends_unknown_and_never_re_runs(
    journal: EdgeOpsJournal,
    clock: ManualClock,
    kind: str,
) -> None:
    op_id = await _queued(journal, kind=kind, key=f"k-{kind}")
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=(kind,), wait_s=0.0)
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    clock.advance(LEASE_TTL_SECONDS + 1.0)

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "unknown"

    regrant = await leasing.lease(journal, edge_id="edge-b", kinds=(kind,), wait_s=0.0)
    assert regrant is None


@pytest.mark.asyncio
async def test_releasing_a_leased_op_returns_it_to_the_queue(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    released = await leasing.release(
        journal, op_id=op_id, edge_id="edge-a", lease_id=grant.lease_id
    )
    assert released["status"] == "queued"

    regrant = await leasing.lease(
        journal, edge_id="edge-b", kinds=("ping",), wait_s=0.0
    )
    assert regrant is not None
    assert regrant.op_id == op_id


@pytest.mark.asyncio
async def test_releasing_a_started_op_records_unknown_instead(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    released = await leasing.release(
        journal, op_id=op_id, edge_id="edge-a", lease_id=grant.lease_id
    )
    assert released["status"] == "unknown"
    assert (
        await leasing.lease(journal, edge_id="edge-b", kinds=("ping",), wait_s=0.0)
        is None
    )


@pytest.mark.asyncio
async def test_directives_long_poll_answers_empty_in_a1(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    pending = await leasing.directives(
        journal, op_id=op_id, edge_id="edge-a", lease_id=grant.lease_id, wait_s=0.0
    )
    assert pending == []


@pytest.mark.asyncio
async def test_directives_refuse_everyone_but_the_lease_holder(
    journal: EdgeOpsJournal,
) -> None:
    """Being a configured edge is not enough to attach to an op's stream.

    An unknown op id refuses identically, so the answer says nothing about
    which ops exist.
    """
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    for target, edge in ((op_id, "edge-b"), ("edgeop-missing", "edge-a")):
        with pytest.raises(EdgeOpRefusedError) as refusal:
            await leasing.directives(
                journal, op_id=target, edge_id=edge, lease_id=grant.lease_id, wait_s=0.0
            )
        assert refusal.value.status_code == 409
        assert refusal.value.code == CODE_LEASE_MISMATCH


@pytest.mark.asyncio
async def test_lifecycle_events_fire_for_each_transition(
    journal: EdgeOpsJournal,
    event_bus: object,
) -> None:
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": True},
    )
    await asyncio.sleep(0)
    signals = set(getattr(event_bus, "signals", []))
    assert {
        "edge.lease.polled",
        "edge.op.admitted",
        "edge.op.leased",
        "edge.op.started",
        "edge.op.completed",
    } <= signals


@pytest.mark.asyncio
async def test_a_never_kind_result_before_started_is_refused(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never")
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    assert grant is not None
    assert grant.redelivery == "never"

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.post_result(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=grant.lease_id,
            result={"ok": True},
        )
    assert refusal.value.code == CODE_NOT_STARTED
    assert refusal.value.status_code == 409

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "leased"
    assert record.started_at_epoch is None
    assert record.result is None


@pytest.mark.asyncio
async def test_a_never_kind_completes_once_started_has_landed(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never")
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    closed = await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": True},
    )
    assert closed["status"] == "completed"

    record = await journal.get(op_id)
    assert record is not None
    assert record.started_at_epoch is not None
    assert record.result == {"ok": True}


@pytest.mark.asyncio
async def test_a_refused_never_kind_result_leaves_the_op_finishable(
    journal: EdgeOpsJournal,
) -> None:
    """The refusal writes nothing, so the same edge can start and then finish."""
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never")
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    with pytest.raises(EdgeOpRefusedError):
        await leasing.post_result(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=grant.lease_id,
            result={"ok": False},
        )
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    closed = await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": True},
    )
    assert closed["status"] == "completed"


@pytest.mark.asyncio
async def test_a_safe_kind_may_result_straight_from_leased(
    journal: EdgeOpsJournal,
) -> None:
    """``ping`` is safe, so its handler need not wait for the ``started`` ack."""
    op_id = await _queued(journal)
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is not None
    assert grant.redelivery == "safe"

    closed = await leasing.post_result(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        result={"ok": True, "pong": True},
    )
    assert closed["status"] == "completed"

    record = await journal.get(op_id)
    assert record is not None
    assert record.started_at_epoch is None


@pytest.mark.asyncio
async def test_a_started_ack_on_an_expired_op_is_refused(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    """The late-ack path: a stalled edge is not told to run after expiry.

    The lease and the short deadline both elapse while the op is leased, so
    the fold on this very call writes ``expired``. Answering 2xx here is what
    let a ``never`` handler run after its caller had been told
    ``edge_op_expired``.
    """
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never", deadline_s=5.0)
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    assert grant is not None
    clock.advance(LEASE_TTL_SECONDS + 1.0)

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.post_event(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=grant.lease_id,
            event_type="started",
        )
    assert refusal.value.status_code == 409
    assert refusal.value.code == CODE_TERMINAL
    assert "expired" in refusal.value.message

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "expired"
    assert record.started_at_epoch is None
    assert record.result is None


@pytest.mark.asyncio
async def test_the_expiry_fold_still_publishes_when_the_ack_is_refused(
    journal: EdgeOpsJournal,
    clock: ManualClock,
    event_bus: object,
) -> None:
    """The refusal must not swallow the fold's own lifecycle event.

    ``await_result`` parks on the transition channel, so a touch that folds an
    op to ``expired`` and then refuses the ack still owes the event and the
    wake-up — otherwise the caller holds its whole budget open while the
    authority has already answered.
    """
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never", deadline_s=5.0)
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    clock.advance(LEASE_TTL_SECONDS + 1.0)
    with pytest.raises(EdgeOpRefusedError):
        await leasing.post_event(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=grant.lease_id,
            event_type="started",
        )
    await asyncio.sleep(0)
    assert "edge.op.expired" in set(getattr(event_bus, "signals", []))


@pytest.mark.asyncio
async def test_posting_started_twice_on_a_live_op_renews_the_lease(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    """The ack stays idempotent on a live op: same state, lease pushed out."""
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never")
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    first = await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    assert first["status"] == "started"

    clock.advance(LEASE_TTL_SECONDS - 1.0)
    again = await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    assert again["status"] == "started"
    assert again["seq"] == first["seq"] + 1

    clock.advance(LEASE_TTL_SECONDS - 1.0)
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "started"


@pytest.mark.asyncio
async def test_a_progress_event_on_a_terminal_op_still_records(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    """Only the ack is refused: narration grants no permission to run.

    The reply names the terminal state, so an edge posting progress into a
    closed op learns it closed without being handed a 2xx that means ``run``.
    """
    op_id = await _queued(journal, kind=NEVER_KIND, key="k-never", deadline_s=5.0)
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    clock.advance(LEASE_TTL_SECONDS + 1.0)
    noted = await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="progress",
    )
    assert noted["status"] == "expired"
