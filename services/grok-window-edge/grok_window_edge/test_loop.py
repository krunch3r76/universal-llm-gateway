"""Edge loop against the real Stargate service functions, in-process.

The loop depends on the ``StargateEdgeClient`` protocol, so this test wires it
straight to ``systems.edge_ops`` with no socket and no HTTP: a ping round trip
that exercises admit, lease, the ``started`` ack and the stored result.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from systems.edge_ops import admission, leasing
from systems.edge_ops.model import EdgeOpRefusedError
from systems.edge_ops.store import EdgeOpsJournal

from grok_window_edge.client import EdgeConflictError, EdgeTransportError, _conflict
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


@contextmanager
def _as_client_error() -> Iterator[None]:
    """Map a Stargate refusal onto the errors ``HttpEdgeClient`` would raise.

    Without this the in-process stand-in would hand the loop an
    ``EdgeOpRefusedError`` that the shipped client can never produce, and the
    tests would exercise a loop nothing in production drives.
    """
    try:
        yield
    except EdgeOpRefusedError as refusal:
        if refusal.status_code == 409:
            raise EdgeConflictError(refusal.message, code=refusal.code) from refusal
        raise EdgeTransportError(
            f"stargate refused with {refusal.status_code}"
        ) from refusal


class InProcessClient:
    """``StargateEdgeClient`` over the Stargate service functions directly.

    Refusals are translated the way ``HttpEdgeClient`` translates statuses: a
    409 becomes ``EdgeConflictError``, anything else a transport fault.
    """

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
        with _as_client_error():
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
        with _as_client_error():
            return await leasing.post_result(
                self._journal,
                op_id=op_id,
                edge_id=self._edge_id,
                result=result,
                outcome=outcome,  # type: ignore[arg-type]
            )

    async def release(self, *, op_id: str) -> dict[str, Any]:
        """Release through the real service function."""
        with _as_client_error():
            return await leasing.release(
                self._journal, op_id=op_id, edge_id=self._edge_id
            )


@pytest.fixture
def clock() -> ManualClock:
    """An injectable clock for the Stargate journal under test."""
    return ManualClock()


@pytest_asyncio.fixture
async def journal(tmp_path: Path, clock: ManualClock) -> EdgeOpsJournal:
    """A Stargate journal on a temporary path with time under test control.

    The poll names both kinds this module admits: Stargate's presence check is
    per kind, so an edge polling only for ``ping`` would make a
    ``fixture-never`` admit fail fast with ``edge_unavailable``.
    """
    store = EdgeOpsJournal(
        path=tmp_path / "edge-ops.db",
        clock=clock,
        lease_ttl_s=LEASE_TTL_SECONDS,
    )
    await store.initialize()
    await store.record_poll(edge_id=EDGE_ID, kinds=("ping", NEVER_KIND))
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
    assert await loop.run_once() is None

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "failed"
    assert record.result["code"] == CODE_KIND_UNSUPPORTED
    assert record.started_at_epoch is not None


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


async def _expired_grant(
    journal: EdgeOpsJournal,
    clock: ManualClock,
    *,
    kind: str,
) -> tuple[str, dict[str, Any], InProcessClient]:
    """Lease one op of *kind*, then let its lease and deadline both elapse.

    The grant the edge still holds in memory is the stalled-process case: by
    the time it acts, Stargate's fold has already closed the op ``expired``.
    """
    op_id = await admission.admit(
        journal,
        kind=kind,
        op=kind,
        args={},
        idempotency_key=f"k-{kind}",
        deadline_s=5.0,
    )
    client = InProcessClient(journal)
    grant = await client.lease(kinds=(kind,), wait_s=0.0)
    assert grant is not None
    assert grant["op_id"] == op_id
    clock.advance(LEASE_TTL_SECONDS + 1.0)
    return op_id, grant, client


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [NEVER_KIND, "ping"])
async def test_no_handler_runs_once_the_op_has_expired(
    journal: EdgeOpsJournal,
    clock: ManualClock,
    kind: str,
) -> None:
    """The falsifiable check for the late-ack path, both redelivery classes.

    Before the fix the ``started`` ack answered 200 with ``status: expired``,
    the loop discarded that body, and the handler ran on an op whose caller
    had already been told ``edge_op_expired`` — the late paste binding 3
    exists to prevent. ``ping`` is here too because the ack is refused on the
    op's state, not on its redelivery class.
    """
    ran: list[str] = []

    async def record_run(op: str, args: dict[str, Any]) -> dict[str, Any]:
        """Handler that reports it ran."""
        ran.append(op)
        return {"ok": True}

    op_id, grant, client = await _expired_grant(journal, clock, kind=kind)
    loop = EdgeLoop(
        client,
        kinds=(kind,),
        handlers={kind: record_run},
        lease_wait_s=0.0,
    )

    await loop._execute(grant)

    assert ran == []
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "expired"
    assert record.started_at_epoch is None
    assert record.result is None


@pytest.mark.asyncio
async def test_nothing_is_posted_once_the_ack_is_refused(
    journal: EdgeOpsJournal,
    clock: ManualClock,
) -> None:
    """The handler-less branch sits behind the same ack, so it stops too.

    Asserting on the stored record alone would prove little here — a terminal
    op ignores a result write anyway — so this counts the calls the loop makes.
    """
    posted: list[str] = []
    op_id, grant, client = await _expired_grant(journal, clock, kind=NEVER_KIND)

    async def count_post(*, op_id: str, result: Any, outcome: str) -> dict[str, Any]:
        """Stand in for the result post and record that it happened."""
        posted.append(op_id)
        return {"op_id": op_id, "status": "completed"}

    client.post_result = count_post  # type: ignore[method-assign]
    loop = EdgeLoop(client, kinds=(NEVER_KIND,), lease_wait_s=0.0)

    await loop._execute(grant)

    assert posted == []
    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "expired"


@pytest.mark.asyncio
async def test_a_refused_write_is_not_an_outage_to_back_off_from(
    journal: EdgeOpsJournal,
) -> None:
    """A 409 is Stargate answering, so the loop re-leases without waiting."""
    slept: list[float] = []
    attempts: list[int] = []

    class ConflictedClient(InProcessClient):
        """A client whose lease call is refused on the op's state."""

        async def lease(
            self,
            *,
            kinds: tuple[str, ...],
            wait_s: float,
        ) -> dict[str, Any] | None:
            """Refuse every lease attempt with a semantic conflict."""
            attempts.append(1)
            raise EdgeConflictError("refused", code="edge_lease_mismatch")

    async def fake_sleep(seconds: float) -> None:
        """Record the backoff instead of waiting it out."""
        slept.append(seconds)

    loop = EdgeLoop(
        ConflictedClient(journal),
        kinds=("ping",),
        lease_wait_s=0.0,
        sleeper=fake_sleep,
    )
    await loop.run_forever(should_stop=lambda: len(attempts) >= 3)

    assert slept == []
    assert len(attempts) == 3


def test_a_409_is_a_conflict_and_not_a_transport_fault() -> None:
    """The shipped client's own mapping, read off a response with no socket."""
    refused = httpx.Response(
        409, json={"error": {"code": "edge_op_terminal", "message": "closed"}}
    )
    conflict = _conflict("POST", "/api/v1/edge/ops/x/events", refused)
    assert isinstance(conflict, EdgeConflictError)
    assert not isinstance(conflict, EdgeTransportError)
    assert conflict.code == "edge_op_terminal"
    assert _conflict("POST", "/x", httpx.Response(409, text="nope")).code is None
