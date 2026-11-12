"""Caller-facing behaviour: admit, idempotency, expiry, await, projection."""

from __future__ import annotations

import pytest

from systems.edge_ops import admission
from systems.edge_ops.model import (
    CODE_EDGE_PENDING,
    CODE_EDGE_UNAVAILABLE,
    CODE_EXPIRED,
    SOURCE_EDGE_OPS_JOURNAL,
    EdgeOpRefusedError,
)
from systems.edge_ops.store import EdgeOpsJournal

from .conftest import NEVER_KIND, ManualClock, seen_edge


@pytest.mark.asyncio
async def test_admit_fails_fast_when_no_edge_has_polled(
    journal: EdgeOpsJournal,
) -> None:
    with pytest.raises(EdgeOpRefusedError) as refusal:
        await admission.admit(
            journal,
            kind="ping",
            op="ping",
            args={},
            idempotency_key="k1",
        )
    assert refusal.value.code == CODE_EDGE_UNAVAILABLE
    assert refusal.value.status_code == 503


@pytest.mark.asyncio
async def test_admit_fails_fast_when_the_last_poll_is_stale(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    await seen_edge(journal)
    clock.advance(121.0)
    with pytest.raises(EdgeOpRefusedError) as refusal:
        await admission.admit(
            journal, kind="ping", op="ping", args={}, idempotency_key="k1"
        )
    assert refusal.value.code == CODE_EDGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_admit_fails_fast_when_no_present_edge_serves_the_kind(
    journal: EdgeOpsJournal,
) -> None:
    """Presence is per kind: a ping-only edge does not make another kind admit.

    Before this, ``admit`` asked only whether *some* edge had polled, so once
    the kinds are split across sibling edges an op nobody serves would sit
    queued until its deadline instead of failing fast.
    """
    await seen_edge(journal, kinds=("ping",))
    with pytest.raises(EdgeOpRefusedError) as refusal:
        await admission.admit(
            journal, kind=NEVER_KIND, op=NEVER_KIND, args={}, idempotency_key="k1"
        )
    assert refusal.value.code == CODE_EDGE_UNAVAILABLE
    assert NEVER_KIND in refusal.value.message

    await seen_edge(journal, edge_id="edge-b", kinds=(NEVER_KIND,))
    op_id = await admission.admit(
        journal, kind=NEVER_KIND, op=NEVER_KIND, args={}, idempotency_key="k1"
    )
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "queued"


@pytest.mark.asyncio
async def test_repeated_idempotency_key_returns_the_same_op(
    journal: EdgeOpsJournal,
) -> None:
    await seen_edge(journal)
    first = await admission.admit(
        journal, kind="ping", op="ping", args={"nonce": "a"}, idempotency_key="k1"
    )
    second = await admission.admit(
        journal, kind="ping", op="ping", args={"nonce": "b"}, idempotency_key="k1"
    )
    assert first == second
    record = await journal.get(first)
    assert record is not None
    assert record.args == {"nonce": "a"}


@pytest.mark.asyncio
async def test_queued_op_expires_at_its_deadline_and_never_runs_late(
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
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "expired"
    grant = await journal.claim_lease(edge_id="edge-a", kinds=("ping",))
    assert grant is None


@pytest.mark.asyncio
async def test_await_result_times_out_with_edge_pending(
    journal: EdgeOpsJournal,
) -> None:
    await seen_edge(journal)
    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    envelope = await admission.await_result(journal, op_id=op_id, timeout_s=0.0)
    assert envelope["ok"] is False
    assert envelope["code"] == CODE_EDGE_PENDING
    assert envelope["op_id"] == op_id


@pytest.mark.asyncio
async def test_await_result_returns_the_handler_dict_verbatim(
    journal: EdgeOpsJournal,
) -> None:
    await seen_edge(journal)
    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    grant = await journal.claim_lease(edge_id="edge-a", kinds=("ping",))
    assert grant is not None
    await journal.post_result(
        op_id=op_id,
        edge_id="edge-a",
        lease_id=grant.lease_id,
        outcome="completed",
        result={"ok": False, "code": "window_unavailable", "detail": "no page"},
    )
    envelope = await admission.await_result(journal, op_id=op_id, timeout_s=0.0)
    assert envelope == {
        "ok": False,
        "code": "window_unavailable",
        "detail": "no page",
        "op_id": op_id,
    }


@pytest.mark.asyncio
async def test_await_result_on_an_expired_op_names_expired(
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
        deadline_s=5.0,
    )
    clock.advance(6.0)
    envelope = await admission.await_result(journal, op_id=op_id, timeout_s=0.0)
    assert envelope["ok"] is False
    assert envelope["code"] == CODE_EXPIRED


@pytest.mark.asyncio
async def test_await_result_clamps_to_the_binding_three_ceiling() -> None:
    from systems.edge_ops import config

    assert config.clamp_await_seconds(600.0) == 30.0
    assert config.AWAIT_MAX_SECONDS == 30.0


@pytest.mark.asyncio
async def test_projection_names_source_scope_and_recovery(
    journal: EdgeOpsJournal,
) -> None:
    await seen_edge(journal)
    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    projection = await admission.get(journal, op_id)
    assert projection["status"] == "queued"
    assert projection["source"] == SOURCE_EDGE_OPS_JOURNAL
    assert projection["scope"] == f"edge_op:{op_id}"
    assert projection["recovery"] == {"consulted": [SOURCE_EDGE_OPS_JOURNAL]}
    assert projection["as_of"].endswith("Z")


@pytest.mark.asyncio
async def test_get_on_an_unknown_op_refuses_with_404(
    journal: EdgeOpsJournal,
) -> None:
    with pytest.raises(EdgeOpRefusedError) as refusal:
        await admission.get(journal, "edgeop-missing")
    assert refusal.value.status_code == 404
