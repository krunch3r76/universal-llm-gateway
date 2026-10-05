"""agent_bus drain probe vs GIW ``/active-work`` while ``/health`` stays up (a:36594).

Specimen: 2026-09-26 08:31Z–08:41Z, three ``sync_restart service=agent_bus`` calls
deferred ``state=probe_error`` with reason ``"could not determine in-flight work: "``.
The Lane-B inventory in ``/active-work`` shells out per ``cursor-sdk/*`` branch
(435 branches measured 15.4s) against the 5s probe budget; ``httpx.ReadTimeout``
stringifies to ``""``.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn

from scripts.model_manager.ui.controller import restart_drain
from scripts.model_manager.ui.controller.restart_drain import (
    HttpActiveWorkProbe,
    RestartDrainGate,
)
from services.git_integration_worker import cursor_sdk_concurrency_meter as meter
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

pytestmark = pytest.mark.offline

_INVENTORY = {
    "worktrees_live": 0,
    "branches_unlanded": 0,
    "oldest_unlanded_age_s": None,
    "aged_orphans": [],
    "lane_hygiene": {},
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def slow_inventory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[threading.Event]:
    """Lane-B inventory that blocks well past the 5s drain-probe budget."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(tmp_path / "repo"))
    CursorDispatchLedger._instance = None
    meter._lane_b_refresh.clear()
    meter._lane_b_cache.clear()
    release = threading.Event()

    def _slow(*, source_repo: Any) -> dict[str, Any]:
        release.wait(30)
        return dict(_INVENTORY)

    monkeypatch.setattr(meter, "lane_b_inventory_snapshot", _slow)
    yield release
    release.set()
    CursorDispatchLedger._instance = None
    meter._lane_b_refresh.clear()
    meter._lane_b_cache.clear()


@pytest.fixture
def giw_url(slow_inventory: threading.Event) -> Iterator[str]:
    """Real GIW app on a TCP port (no lifespan — same surface as the route tests)."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(), host="127.0.0.1", port=port, lifespan="off", log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    slow_inventory.set()
    server.should_exit = True
    thread.join(timeout=10)


def test_agent_bus_drain_probe_determinate_while_inventory_slow(
    giw_url: str, slow_inventory: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health up + slow inventory ⇒ agent_bus probe answers inside the 5s budget."""
    monkeypatch.setattr(restart_drain, "GIT_INTEGRATION_WORKER_URL", giw_url)
    monkeypatch.setattr(restart_drain, "cdp_ask_url_config", lambda: None)
    probe = restart_drain._default_probes()["agent_bus"]
    assert isinstance(probe, HttpActiveWorkProbe)
    gate = RestartDrainGate(probes={"agent_bus": probe})

    async def _scenario() -> tuple[dict[str, Any], float, Any, Any]:
        async with httpx.AsyncClient(base_url=giw_url, timeout=2.0) as client:
            health = (await client.get("/health")).json()
        t0 = time.monotonic()
        outcome = await gate.evaluate("agent_bus", force=False)
        elapsed = time.monotonic() - t0
        if outcome is None:
            await gate.release("agent_bus")
        work = await gate.probe("agent_bus")
        slow_inventory.set()
        fresh = None
        for _ in range(40):
            fresh = await gate.probe("agent_bus")
            if fresh.detail.get("lane_b_status") == "fresh":
                break
            await asyncio.sleep(0.05)
        return health, elapsed, outcome, (work, fresh)

    health, elapsed, outcome, (work, fresh) = asyncio.run(_scenario())

    assert health["status"] == "ok"
    assert elapsed < restart_drain._PROBE_TIMEOUT_S, elapsed
    assert outcome is None or outcome.state == "busy", (
        outcome.to_result() if outcome else None
    )
    assert work.busy is False
    assert work.detail["lane_b_status"] == "pending"
    assert work.detail["lane_b"] == {"status": "pending"}
    assert isinstance(work.detail["lane_b_regime"], bool)
    # a:37853: occupancy must not await inventory — probe stays well under 1s.
    assert elapsed < 1.0, elapsed
    assert fresh is not None and fresh.detail["lane_b_status"] == "fresh"
    assert fresh.detail["lane_b"] == _INVENTORY
    assert fresh.detail["lane_b_as_of"]
    print(f"agent_bus drain probe elapsed_s={elapsed:.2f} outcome={outcome}")


def test_probe_timeout_reason_names_exception_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real httpx read timeout (empty ``str``) still names its class in the deferral."""

    async def _scenario() -> tuple[dict[str, Any], dict[str, Any], str]:
        async def _hang(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await asyncio.sleep(30)

        server = await asyncio.start_server(_hang, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(restart_drain, "_PROBE_TIMEOUT_S", 0.3)
        probe = HttpActiveWorkProbe(
            f"http://127.0.0.1:{port}", "/api/v1/git/active-work"
        )
        try:
            await probe.snapshot()
        except httpx.ReadTimeout as exc:
            raw = str(exc)
        else:
            raise AssertionError("expected ReadTimeout")
        gate = RestartDrainGate(probes={"agent_bus": probe})
        try:
            outcome = await gate.evaluate("agent_bus", force=False)
            report = await gate.busy_report(["agent_bus"])
        finally:
            server.close()
        assert outcome is not None
        return outcome.to_result(), report["agent_bus"], raw

    result, report, raw = asyncio.run(_scenario())

    assert raw == ""
    assert result["state"] == "probe_error"
    assert result["determination"] == "undetermined"
    assert result["reason"] == "could not determine in-flight work: ReadTimeout"
    assert report["restart_would_defer"] is True
    assert report["determination"] == "undetermined"
    assert report["busy"] is False
    assert report["active_work"]["error"] == "ReadTimeout"
    print(
        f"reason={result['reason']!r} busy_report_error={report['active_work']['error']!r}"
    )


def test_describe_probe_exc_keeps_message_when_present() -> None:
    exc = httpx.ConnectError("All connection attempts failed")
    assert (
        restart_drain.describe_probe_exc(exc)
        == "ConnectError: All connection attempts failed"
    )


def test_active_lease_index_avoids_full_scan_on_holder_lookup(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:37853: instance() wires idx_sdk_dispatch_active_lease; holder SELECT uses it."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_sdk_dispatch_active_lease" in names
        plan = list(
            conn.execute(
                "EXPLAIN QUERY PLAN "
                "SELECT dispatch_id FROM cursor_sdk_dispatches "
                "WHERE lease_key=? AND COALESCE(read_only,0)=0 "
                "AND status IN ('admitted','running') LIMIT 1",
                ("/tmp/repo",),
            )
        )
    CursorDispatchLedger._instance = None
    detail = " ".join(str(row[-1]) for row in plan).lower()
    assert "idx_sdk_dispatch_active_lease" in detail, plan
    assert "scan cursor_sdk_dispatches" not in detail, plan


def test_active_work_busy_status_sees_lane_b_holder(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1: Lane-B lease_key is a worktree path — busy_status must stay global."""
    from fastapi.testclient import TestClient

    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    repo = tmp_path / "source"
    worktree = tmp_path / "worktrees" / "lane-b"
    repo.mkdir()
    worktree.mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(repo))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="t-lane-b",
        model="cursor/composer-2.5",
        dispatch_id="lane-b-holder",
        execution_id="exec-lane-b",
        message="lane-b subject",
        lane="B",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(repo),
        lease_key=str(worktree),
        contract="implement",
        read_only=False,
    )
    ledger.mark_running(dispatch_id="lane-b-holder")
    with TestClient(create_app()) as client:
        data = client.get("/api/v1/git/active-work").json()
    CursorDispatchLedger._instance = None
    busy = data["cursor_sdk_gate"]["busy_status"]
    assert busy["active_holder"]["dispatch_id"] == "lane-b-holder"
    assert data["write_lease"]["holder_dispatch_id"] is None
