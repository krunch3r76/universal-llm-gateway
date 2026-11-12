"""Edge loop against the real Stargate service functions, in-process.

The loop depends on the ``StargateEdgeClient`` protocol, so this test wires it
straight to ``systems.edge_ops`` with no socket and no HTTP: a ping round trip
that exercises admit, lease, the ``started`` ack and the stored result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from systems.edge_ops import admission, leasing
from systems.edge_ops.store import EdgeOpsJournal

from grok_window_edge.client import EdgeTransportError
from grok_window_edge.loop import (
    CODE_HANDLER_EXCEPTION,
    CODE_KIND_UNSUPPORTED,
    EdgeLoop,
)
from grok_window_edge.registry import HANDLERS

EDGE_ID = "edge-a"
NEVER_KIND = "fixture-never"
LEASE_TTL_SECONDS = 30.0


class ManualClock:
    """A clock the test advances explicitly."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        """Move time forward and return the new value."""
        self.value += seconds
        return self.value


class InProcessClient:
    """``StargateEdgeClient`` over the Stargate service functions directly."""

    def __init__(self, journal: EdgeOpsJournal, *, edge_id: str = EDGE_ID) -> None:
        self._journal = journal
        self._edge_id = edge_id
        self.refuse_started = False

    async def lease(
        self,
        *,
        kinds: tuple[str, ...],
        wait_s: float,
    ) -> dict[str, Any] | None:
        """Lease through the real service function."""
        grant = await leasing.lease(
            self._journal, edge_id=self._edge_id, kinds=kinds, wait_s=0.0
        )
        return None if grant is None else grant.to_dict()

    async def post_event(
        self,
        *,
        op_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Post an event, or simulate Stargate being unreachable."""
        if self.refuse_started:
            raise EdgeTransportError("simulated Stargate outage")
        return await leasing.post_event(
            self._journal,
            op_id=op_id,
            edge_id=self._edge_id,
            event_type=event_type,
            payload=payload,
        )

    async def post_result(
        self,
        *,
        op_id: str,
        result: Any,
        outcome: str,
    ) -> dict[str, Any]:
        """Post the result through the real service function."""
        return await leasing.post_result(
            self._journal,
            op_id=op_id,
            edge_id=self._edge_id,
            result=result,
            outcome=outcome,  # type: ignore[arg-type]
        )

    async def release(self, *, op_id: str) -> dict[str, Any]:
        """Release through the real service function."""
        return await leasing.release(self._journal, op_id=op_id, edge_id=self._edge_id)


@pytest.fixture
def clock() -> ManualClock:
    """An injectable clock for the Stargate journal under test."""
    return ManualClock()


@pytest_asyncio.fixture
async def journal(tmp_path: Path, clock: ManualClock) -> EdgeOpsJournal:
    """A Stargate journal on a temporary path with time under test control."""
    store = EdgeOpsJournal(
        path=tmp_path / "edge-ops.db",
        clock=clock,
        lease_ttl_s=LEASE_TTL_SECONDS,
    )
    await store.initialize()
    await store.record_poll(edge_id=EDGE_ID, kinds=("ping",))
    return store


@pytest.mark.asyncio
async def test_a_ping_round_trips_between_the_edge_and_stargate(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await admission.admit(
        journal,
        kind="ping",
        op="ping",
        args={"nonce": "n-1"},
        idempotency_key="k1",
    )
    loop = EdgeLoop(InProcessClient(journal), kinds=("ping",), lease_wait_s=0.0)
    assert await loop.run_once() == op_id

    envelope = await admission.await_result(journal, op_id=op_id, timeout_s=0.0)
    assert envelope == {
        "ok": True,
        "pong": True,
        "op": "ping",
        "nonce": "n-1",
        "op_id": op_id,
    }
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "completed"
    assert record.started_at_epoch is not None


@pytest.mark.asyncio
async def test_an_idle_lease_returns_nothing_and_runs_no_handler(
    journal: EdgeOpsJournal,
) -> None:
    loop = EdgeLoop(InProcessClient(journal), kinds=("ping",), lease_wait_s=0.0)
    assert await loop.run_once() is None


@pytest.mark.asyncio
async def test_a_never_kind_does_not_run_without_a_started_ack(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    ran: list[str] = []

    async def record_run(op: str, args: dict[str, Any]) -> dict[str, Any]:
        """Handler that reports it ran."""
        ran.append(op)
        return {"ok": True}

    op_id = await admission.admit(
        journal,
        kind=NEVER_KIND,
        op=NEVER_KIND,
        args={},
        idempotency_key="k1",
    )
    client = InProcessClient(journal)
    client.refuse_started = True
    loop = EdgeLoop(
        client,
        kinds=(NEVER_KIND,),
        handlers={NEVER_KIND: record_run},
        lease_wait_s=0.0,
    )
    await loop.run_once()

    assert ran == []
    clock.advance(LEASE_TTL_SECONDS + 1.0)
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "queued"


@pytest.mark.asyncio
async def test_a_handler_exception_posts_a_failed_result(
    journal: EdgeOpsJournal,
) -> None:
    async def boom(op: str, args: dict[str, Any]) -> dict[str, Any]:
        """Handler that always raises."""
        raise RuntimeError("no composer")

    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    loop = EdgeLoop(
        InProcessClient(journal),
        kinds=("ping",),
        handlers={"ping": boom},
        lease_wait_s=0.0,
    )
    await loop.run_once()

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "failed"
    assert record.result["code"] == CODE_HANDLER_EXCEPTION


@pytest.mark.asyncio
async def test_an_unsupported_kind_fails_once_without_a_retry(
    journal: EdgeOpsJournal,
) -> None:
    op_id = await admission.admit(
        journal,
        kind=NEVER_KIND,
        op=NEVER_KIND,
        args={},
        idempotency_key="k1",
    )
    loop = EdgeLoop(InProcessClient(journal), kinds=(NEVER_KIND,), lease_wait_s=0.0)
    await loop.run_once()

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "failed"
    assert record.result["code"] == CODE_KIND_UNSUPPORTED


@pytest.mark.asyncio
async def test_the_loop_backs_off_instead_of_retrying_the_op(
    journal: EdgeOpsJournal,
) -> None:
    slept: list[float] = []
    attempts: list[int] = []

    class UnreachableClient(InProcessClient):
        """A client whose lease call always fails."""

        async def lease(
            self,
            *,
            kinds: tuple[str, ...],
            wait_s: float,
        ) -> dict[str, Any] | None:
            """Fail every lease attempt."""
            attempts.append(1)
            raise EdgeTransportError("stargate down")

    async def fake_sleep(seconds: float) -> None:
        """Record the backoff instead of waiting it out."""
        slept.append(seconds)

    loop = EdgeLoop(
        UnreachableClient(journal),
        kinds=("ping",),
        lease_wait_s=0.0,
        sleeper=fake_sleep,
    )
    await loop.run_forever(should_stop=lambda: len(slept) >= 3)

    assert slept == [1.0, 2.0, 4.0]
    assert len(attempts) == 3


def test_only_ping_is_registered_in_a1() -> None:
    assert set(HANDLERS) == {"ping"}
