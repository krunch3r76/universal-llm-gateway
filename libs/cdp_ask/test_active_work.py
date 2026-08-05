"""Hermetic tests for cdp-ask active-work drain + lane-admission probe."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.execution_store import (
    LANE_HARD_LIMIT,
    LANE_SOFT_LIMIT,
    ExecutionStore,
)

pytestmark = pytest.mark.offline


def _capacity(
    *,
    busy: bool,
    running_count: int,
    execution_ids: list[str],
    rows: list[dict[str, object]] | None = None,
    live_cse_count: int = 0,
) -> dict[str, Any]:
    effective = max(running_count, live_cse_count)
    return {
        "busy": busy,
        "running_count": running_count,
        "running_count_scope": "cdp_ask execution store, pending/running records",
        "running_count_authority": "recorded",
        "live_cse_count": live_cse_count,
        "live_cse_count_scope": "browser CSE lanes, this host",
        "live_cse_count_authority": "observed",
        "effective_count": effective,
        "effective_count_scope": "max(running_count, live_cse_count), this host",
        "effective_count_authority": "max(recorded, observed)",
        "execution_ids": execution_ids,
        "rows": rows or [],
        "soft_limit": LANE_SOFT_LIMIT,
        "hard_limit": LANE_HARD_LIMIT,
        "free_slots": max(0, LANE_HARD_LIMIT - effective),
        "at_soft_limit": effective >= LANE_SOFT_LIMIT,
        "at_hard_limit": effective >= LANE_HARD_LIMIT,
    }


@pytest.mark.asyncio
async def test_active_work_snapshot_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    snap = await store.active_work_snapshot()
    assert snap == _capacity(busy=False, running_count=0, execution_ids=[])


@pytest.mark.asyncio
async def test_active_work_snapshot_pending_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    record = await store.create(holder="test", purpose="ask")
    snap = await store.active_work_snapshot()
    assert snap == _capacity(
        busy=True,
        running_count=1,
        execution_ids=[record.execution_id],
        rows=[
            {
                "execution_id": record.execution_id,
                "registration_id": None,
                "holder": "test",
                "purpose": "ask",
                "status": "pending",
            }
        ],
    )
    # One lane in flight must NOT read as admission-full (soft=2, hard=3).
    assert snap["at_soft_limit"] is False
    assert snap["at_hard_limit"] is False
    assert snap["free_slots"] == LANE_HARD_LIMIT - 1


@pytest.mark.asyncio
async def test_active_work_snapshot_capacity_soft_and_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    ids: list[str] = []
    rows: list[dict[str, object]] = []
    for _ in range(LANE_SOFT_LIMIT):
        rec = await store.create(holder="test", purpose="ask")
        ids.append(rec.execution_id)
        rows.append(
            {
                "execution_id": rec.execution_id,
                "registration_id": None,
                "holder": "test",
                "purpose": "ask",
                "status": "pending",
            }
        )
    soft_snap = await store.active_work_snapshot()
    assert soft_snap["at_soft_limit"] is True
    assert soft_snap["at_hard_limit"] is False
    assert soft_snap["free_slots"] == LANE_HARD_LIMIT - LANE_SOFT_LIMIT

    for _ in range(LANE_HARD_LIMIT - LANE_SOFT_LIMIT):
        rec = await store.create(holder="test", purpose="ask")
        ids.append(rec.execution_id)
        rows.append(
            {
                "execution_id": rec.execution_id,
                "registration_id": None,
                "holder": "test",
                "purpose": "ask",
                "status": "pending",
            }
        )
    hard_snap = await store.active_work_snapshot()
    assert hard_snap == _capacity(
        busy=True,
        running_count=LANE_HARD_LIMIT,
        execution_ids=ids,
        rows=rows,
    )


@pytest.mark.asyncio
async def test_active_work_snapshot_ignores_terminal_executions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    record = await store.create(holder="test", purpose="ask")
    await store.mark_terminal(
        record.execution_id, status="completed", result={"ok": True}
    )
    snap = await store.active_work_snapshot()
    assert snap == _capacity(busy=False, running_count=0, execution_ids=[])


def test_active_work_endpoint_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "cdp_ask.app.verify_harvest_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    app = create_app(store=store)
    with TestClient(app) as client:
        resp = client.get("/v1/project-ask/active-work")
    assert resp.status_code == 200
    assert resp.json() == _capacity(busy=False, running_count=0, execution_ids=[])


@pytest.mark.asyncio
async def test_active_work_endpoint_busy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "cdp_ask.app.verify_harvest_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.list_active",
        lambda: [],
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    store = ExecutionStore()
    record = await store.create(holder="holder", purpose="harvest")
    app = create_app(store=store)
    with TestClient(app) as client:
        resp = client.get("/v1/project-ask/active-work")
    data = resp.json()
    assert data == _capacity(
        busy=True,
        running_count=1,
        execution_ids=[record.execution_id],
        rows=[
            {
                "execution_id": record.execution_id,
                "registration_id": None,
                "holder": "holder",
                "purpose": "harvest",
                "status": "pending",
            }
        ],
    )


@pytest.mark.asyncio
async def test_active_work_snapshot_observed_population_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cdp_orphans import LivePort

    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [
            LivePort(
                port=9229 + i,
                profile=None,
                page_urls=("https://claude.ai/cowork/cse_x",),
                has_live_cse=True,
            )
            for i in range(7)
        ],
    )
    store = ExecutionStore()
    snap = await store.active_work_snapshot()
    assert snap["running_count"] == 0
    assert snap["live_cse_count"] == 7
    assert snap["busy"] is True
    assert snap["free_slots"] == 0
    assert snap["at_hard_limit"] is True


@pytest.mark.asyncio
async def test_active_work_snapshot_probe_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _probe(port_range=None):  # noqa: ANN001
        calls["n"] += 1
        return []

    monkeypatch.setattr("claude_bundles.cdp_orphans.probe_live_ports", _probe)
    store = ExecutionStore()
    await store.active_work_snapshot()
    await store.active_work_snapshot()
    assert calls["n"] == 1
