"""A1 lease precondition: ``lease_id`` binding and compare-and-set writes."""

from __future__ import annotations

import asyncio

import pytest

from systems.edge_ops import admission, leasing
from systems.edge_ops.model import (
    CODE_LEASE_STALE,
    CODE_TERMINAL,
    EdgeOpRefusedError,
)
from systems.edge_ops.store import EdgeOpsJournal
from systems.edge_ops.store.schema import fold_due_sync, open_connection

from .conftest import LEASE_TTL_SECONDS, NEVER_KIND, ManualClock, seen_edge


@pytest.mark.asyncio
async def test_stale_lease_id_is_refused_for_every_write(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    op_id = await seen_edge_and_admit(journal, kind="ping", key="stale-1")
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is not None
    stale_id = grant.lease_id
    clock.advance(LEASE_TTL_SECONDS + 1.0)
    regrant = await leasing.lease(
        journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0
    )
    assert regrant is not None
    assert regrant.op_id == op_id
    assert regrant.lease_id != stale_id

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.post_event(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=stale_id,
            event_type="started",
        )
    assert refusal.value.code == CODE_LEASE_STALE

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.post_event(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=stale_id,
            event_type="progress",
            payload={},
        )
    assert refusal.value.code == CODE_LEASE_STALE

    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=regrant.lease_id,
        event_type="started",
    )

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.post_result(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=stale_id,
            result={"ok": True},
        )
    assert refusal.value.code == CODE_LEASE_STALE

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.release(journal, op_id=op_id, edge_id="edge-a", lease_id=stale_id)
    assert refusal.value.code == CODE_LEASE_STALE

    with pytest.raises(EdgeOpRefusedError) as refusal:
        await leasing.directives(
            journal,
            op_id=op_id,
            edge_id="edge-a",
            lease_id=stale_id,
            wait_s=0.0,
        )
    assert refusal.value.code == CODE_LEASE_STALE


@pytest.mark.asyncio
async def test_fold_expiry_wins_a_race_without_overwriting_terminal_state(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    op_id = await seen_edge_and_admit(
        journal, kind=NEVER_KIND, key="race-1", deadline_s=5.0
    )
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
    assert refusal.value.code == CODE_TERMINAL

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "expired"


def test_fold_update_is_state_guarded_on_one_connection(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    """Simulate a concurrent fold by forcing expiry while state is still ``leased``."""
    now = clock()
    with open_connection(journal.path) as connection:
        connection.execute(
            "INSERT INTO edge_polls (edge_id, last_poll_epoch, kinds_json) "
            "VALUES ('edge-a', ?, '[\"ping\"]')",
            (now,),
        )
        connection.execute(
            "INSERT INTO edge_ops (op_id, kind, op, args_json, redelivery, "
            "idempotency_key, state, admitted_at_epoch, deadline_epoch, "
            "dedupe_until_epoch, leased_by, lease_id, lease_expires_epoch, "
            "updated_at_epoch) VALUES (?, 'ping', 'ping', '{}', 'safe', 'k', "
            "'leased', ?, ?, ?, 'edge-a', 'lease-old', ?, ?)",
            ("race-op", now, now + 5.0, now + 400.0, now - 1.0, now),
        )
        connection.commit()
        clock.advance(LEASE_TTL_SECONDS + 2.0)
        folded = fold_due_sync(connection, clock())
        assert folded
        row = connection.execute(
            "SELECT state FROM edge_ops WHERE op_id = 'race-op'"
        ).fetchone()
        assert row["state"] == "expired"
        stale = connection.execute(
            "UPDATE edge_ops SET state = 'started', updated_at_epoch = ? "
            "WHERE op_id = 'race-op' AND state = 'leased'",
            (clock(),),
        ).rowcount
        assert stale == 0


@pytest.mark.asyncio
async def test_refused_started_emits_start_refused_event(
    journal: EdgeOpsJournal,
    clock: ManualClock,
    event_bus: object,
) -> None:
    op_id = await seen_edge_and_admit(
        journal, kind=NEVER_KIND, key="evt", deadline_s=5.0
    )
    grant = await leasing.lease(
        journal, edge_id="edge-a", kinds=(NEVER_KIND,), wait_s=0.0
    )
    assert grant is not None
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
    assert "edge.op.start_refused" in set(getattr(event_bus, "signals", []))


@pytest.mark.asyncio
async def test_started_past_deadline_writes_cancel_directive_only(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    op_id = await seen_edge_and_admit(
        journal, kind="ping", key="cancel", deadline_s=5.0
    )
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is not None
    await leasing.post_event(
        journal,
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        event_type="started",
    )
    clock.advance(6.0)
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "started"
    directives = await journal.directives_after(op_id=op_id, after=0)
    assert len(directives) == 1
    assert directives[0]["type"] == "cancel"


async def seen_edge_and_admit(
    journal: EdgeOpsJournal,
    *,
    kind: str,
    key: str,
    deadline_s: float = 300.0,
) -> str:
    await seen_edge(journal, kinds=(kind,))
    return await admission.admit(
        journal,
        kind=kind,
        op=kind,
        args={"nonce": key},
        idempotency_key=key,
        deadline_s=deadline_s,
    )
