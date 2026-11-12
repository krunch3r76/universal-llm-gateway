"""Restart and reconcile: the journal is the authority, the wake-ups are hints.

A second ``EdgeOpsJournal`` over the same file is what a restarted Stargate
sees. It must read the same states and apply the same lease-loss rules from
the stored timestamps alone, with no handover of in-memory state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.edge_ops import admission, leasing
from systems.edge_ops.store import EdgeOpsJournal

from .conftest import LEASE_TTL_SECONDS, ManualClock, RecordingEventBus, seen_edge


def _reopened(path: Path, clock: ManualClock) -> EdgeOpsJournal:
    """A fresh journal instance over an existing store, as after a restart."""
    return EdgeOpsJournal(
        path=path,
        clock=clock,
        event_bus=RecordingEventBus(),
        lease_ttl_s=LEASE_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_a_restarted_journal_sees_the_same_states(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    await seen_edge(journal)
    queued = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k-queued"
    )
    done = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k-done"
    )
    await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    await leasing.post_result(
        journal, op_id=queued, edge_id="edge-a", result={"ok": True, "first": True}
    )

    restarted = _reopened(journal.path, clock)
    first = await restarted.get(queued)
    second = await restarted.get(done)
    assert first is not None
    assert first.state == "completed"
    assert first.result == {"ok": True, "first": True}
    assert second is not None
    assert second.state == "queued"


@pytest.mark.asyncio
async def test_a_restarted_journal_requeues_a_lease_lost_before_started(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    await seen_edge(journal)
    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)

    clock.advance(LEASE_TTL_SECONDS + 1.0)
    restarted = _reopened(journal.path, clock)
    record = await restarted.get(op_id)
    assert record is not None
    assert record.state == "queued"

    regrant = await leasing.lease(
        restarted, edge_id="edge-b", kinds=("ping",), wait_s=0.0
    )
    assert regrant is not None
    assert regrant.op_id == op_id


@pytest.mark.asyncio
async def test_a_restarted_journal_marks_a_lease_lost_after_started_unknown(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    await seen_edge(journal)
    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    await leasing.post_event(
        journal, op_id=op_id, edge_id="edge-a", event_type="started"
    )

    clock.advance(LEASE_TTL_SECONDS + 1.0)
    restarted = _reopened(journal.path, clock)
    record = await restarted.get(op_id)
    assert record is not None
    assert record.state == "unknown"


@pytest.mark.asyncio
async def test_a_restarted_journal_expires_an_op_that_outlived_its_deadline(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    await seen_edge(journal)
    op_id = await admission.admit(
        journal,
        kind="ping",
        op="ping",
        args={},
        idempotency_key="k1",
        deadline_s=10.0,
    )
    clock.advance(11.0)
    restarted = _reopened(journal.path, clock)
    record = await restarted.get(op_id)
    assert record is not None
    assert record.state == "expired"


@pytest.mark.asyncio
async def test_a_journal_reached_without_initialize_still_answers(
    tmp_path: Path,
    clock: ManualClock,
) -> None:
    """A fresh store with no boot-time ``initialize`` must not raise.

    ``runtime.journal_for`` can hand a request a journal that no boot sequence
    touched, so the first read has to create the schema rather than fail on a
    missing table.
    """
    fresh = _reopened(tmp_path / "unborn.db", clock)
    assert await fresh.get("edgeop-missing") is None
    assert await fresh.edge_seen_recently(kind="ping") is False
