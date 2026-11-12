"""The journal the routes actually build, and whether it publishes.

A1 ships no boot wiring, so every ``/api/v1/edge/*`` request lands on the
fallback journal from ``runtime.default_journal``. These cases drive
``journal_for`` with the app shape production has — a proxy carrying an
``event_bus``, reached through the app's own ``get_proxy`` dependency — and
assert that the ``edge.op.*`` vocabulary reaches that bus. A journal built with
``event_bus=None`` passes every other unit test in this package and publishes
nothing at runtime; that is the regression these cases exist to catch.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request

from systems.edge_ops import admission, leasing, runtime
from systems.edge_ops.store import EdgeOpsJournal
from systems.proxy import dependencies

from .conftest import RecordingEventBus

LIFECYCLE_SIGNALS = frozenset(
    {
        "edge.lease.polled",
        "edge.op.admitted",
        "edge.op.leased",
        "edge.op.started",
        "edge.op.completed",
    }
)


class StubProxy:
    """A proxy carrying the two attributes ``journal_for`` reads off one."""

    def __init__(
        self,
        event_bus: RecordingEventBus | None = None,
        edge_ops_journal: EdgeOpsJournal | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.edge_ops_journal = edge_ops_journal


def _request(app: FastAPI) -> Request:
    """A request carrying *app*, which is all ``journal_for`` reads from one."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/edge/leases",
            "headers": [],
            "app": app,
        }
    )


@pytest.fixture
def fallback_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Send the fallback journal to a temporary ``DATA_DIR``, cleared after."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    runtime.set_default_journal(None)
    try:
        yield
    finally:
        runtime.set_default_journal(None)


async def _admit_lease_and_finish(journal: EdgeOpsJournal) -> str:
    """Walk one op through the transitions the Event Service needs to see."""
    idle = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert idle is None
    op_id = await admission.admit(
        journal, kind="ping", op="ping", args={}, idempotency_key="k1"
    )
    grant = await leasing.lease(journal, edge_id="edge-a", kinds=("ping",), wait_s=0.0)
    assert grant is not None
    await leasing.post_event(
        journal, op_id=op_id, edge_id="edge-a", event_type="started"
    )
    await leasing.post_result(
        journal, op_id=op_id, edge_id="edge-a", result={"ok": True}
    )
    await asyncio.sleep(0)
    return op_id


@pytest.mark.asyncio
async def test_the_journal_the_routes_build_publishes_through_the_proxy_bus(
    fallback_home: None,
    event_bus: RecordingEventBus,
) -> None:
    app = FastAPI()
    app.dependency_overrides[dependencies.get_proxy] = lambda: StubProxy(event_bus)

    journal = runtime.journal_for(_request(app))
    await _admit_lease_and_finish(journal)

    assert LIFECYCLE_SIGNALS <= set(event_bus.signals)


@pytest.mark.asyncio
async def test_the_initialized_proxy_singleton_is_the_production_resolution(
    fallback_home: None,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production registers no override, so ``get_proxy`` answers directly."""
    monkeypatch.setattr(dependencies, "_proxy_instance", StubProxy(event_bus))

    journal = runtime.journal_for(_request(FastAPI()))
    await _admit_lease_and_finish(journal)

    assert LIFECYCLE_SIGNALS <= set(event_bus.signals)


@pytest.mark.asyncio
async def test_a_request_before_any_proxy_exists_still_answers_from_the_store(
    fallback_home: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No proxy is not an error here: the store answers, nothing publishes."""
    monkeypatch.setattr(dependencies, "_proxy_instance", None)

    journal = runtime.journal_for(_request(FastAPI()))
    op_id = await _admit_lease_and_finish(journal)

    record = await journal.get(op_id)
    assert record is not None
    assert record.state == "completed"


@pytest.mark.asyncio
async def test_a_bus_already_injected_is_never_replaced(
    fallback_home: None,
    event_bus: RecordingEventBus,
    tmp_path: Path,
) -> None:
    """A journal wired with a bus keeps it; the proxy's does not displace it."""
    wired = EdgeOpsJournal(path=tmp_path / "wired.db", event_bus=event_bus)
    runtime.set_default_journal(wired)
    app = FastAPI()
    app.dependency_overrides[dependencies.get_proxy] = lambda: StubProxy(
        RecordingEventBus()
    )

    journal = runtime.journal_for(_request(app))

    assert journal is wired
    await _admit_lease_and_finish(journal)
    assert LIFECYCLE_SIGNALS <= set(event_bus.signals)


@pytest.mark.asyncio
async def test_a_journal_attached_to_the_proxy_wins_over_the_fallback(
    fallback_home: None,
    journal: EdgeOpsJournal,
) -> None:
    """``proxy.edge_ops_journal`` is the hook a boot step would install."""
    app = FastAPI()
    app.dependency_overrides[dependencies.get_proxy] = lambda: StubProxy(
        edge_ops_journal=journal
    )

    assert runtime.journal_for(_request(app)) is journal
