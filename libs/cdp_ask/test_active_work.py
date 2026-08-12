"""Hermetic tests for cdp-ask active-work drain + stream-admission probe."""

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
from cdp_ask.lane_admission import (
    ADVISOR_RESERVE,
    admission_regime,
    count_by_purpose_class,
    effective_abs_hard,
)

pytestmark = pytest.mark.offline


def _row(
    *,
    execution_id: str,
    registration_id: str | None = None,
    holder: str = "test",
    purpose: str = "ask",
    status: str = "pending",
    cdp_url: str | None = None,
    chat_url: str | None = None,
    source: str | None = None,
) -> dict[str, object]:
    return {
        "execution_id": execution_id,
        "registration_id": registration_id,
        "holder": holder,
        "purpose": purpose,
        "status": status,
        "cdp_url": cdp_url,
        "chat_url": chat_url,
        "source": source,
    }


def _capacity(
    *,
    busy: bool,
    running_count: int,
    execution_ids: list[str],
    rows: list[dict[str, object]] | None = None,
    live_cse_count: int = 0,
    registry_capacity_count: int = 0,
) -> dict[str, Any]:
    admission_count = running_count
    effective = max(running_count, live_cse_count)
    rows_list = list(rows or [])
    seat_count, other_count = count_by_purpose_class(rows_list)
    regime = admission_regime(seat_count)
    abs_hard_effective = effective_abs_hard(seat_count)
    return {
        "busy": busy,
        "running_count": running_count,
        "running_count_scope": "cdp_ask execution store, pending/running streams",
        "running_count_authority": "recorded",
        "admission_count": admission_count,
        "admission_count_scope": "running/stream admissions, this host (soft=2 hard=3)",
        "admission_count_authority": "recorded",
        "live_cse_count": live_cse_count,
        "live_cse_count_scope": "open CSE attachments (Chrome pages), this host",
        "live_cse_count_authority": "observed",
        "registry_capacity_count": registry_capacity_count,
        "registry_capacity_count_scope": (
            "active registry Chrome hosts (ports/profiles), this host"
        ),
        "registry_capacity_count_authority": "recorded",
        "effective_count": effective,
        "effective_count_scope": (
            "restart-drain aggregate max(running_count, live_cse_count); NOT admission"
        ),
        "effective_count_authority": "max(recorded, observed)",
        "execution_ids": execution_ids,
        "rows": rows or [],
        "soft_limit": LANE_SOFT_LIMIT,
        "hard_limit": LANE_HARD_LIMIT,
        "free_slots": max(0, abs_hard_effective - admission_count),
        "at_soft_limit": admission_count >= LANE_SOFT_LIMIT,
        "at_hard_limit": admission_count >= abs_hard_effective,
        "seat_count": seat_count,
        "other_count": other_count,
        "advisor_reserve": ADVISOR_RESERVE,
        "admission_regime": regime,
        "effective_abs_hard": abs_hard_effective,
    }


@pytest.fixture(autouse=True)
def _no_registry_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.count_capacity_lanes",
        lambda: 0,
    )


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
            _row(
                execution_id=record.execution_id,
                registration_id=None,
            )
        ],
    )
    # One stream in flight must NOT read as admission-full (soft=2, hard=3).
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
            _row(
                execution_id=rec.execution_id,
                registration_id=None,
            )
        )
    soft_snap = await store.active_work_snapshot()
    assert soft_snap["at_soft_limit"] is True
    assert soft_snap["at_hard_limit"] is False
    assert soft_snap["free_slots"] == LANE_HARD_LIMIT - LANE_SOFT_LIMIT

    for _ in range(LANE_HARD_LIMIT - LANE_SOFT_LIMIT):
        rec = await store.create(holder="test", purpose="ask")
        ids.append(rec.execution_id)
        rows.append(
            _row(
                execution_id=rec.execution_id,
                registration_id=None,
            )
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
            _row(
                execution_id=record.execution_id,
                registration_id=None,
                holder="holder",
                purpose="harvest",
            )
        ],
    )


@pytest.mark.asyncio
async def test_active_work_snapshot_idle_attachments_do_not_fill_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arc 6885: open idle CSE tabs are hygiene, not stream admission."""
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
    assert snap["admission_count"] == 0
    assert snap["live_cse_count"] == 7
    assert snap["busy"] is True  # drain still sees attachments
    assert snap["free_slots"] == LANE_HARD_LIMIT
    assert snap["at_soft_limit"] is False
    assert snap["at_hard_limit"] is False


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


@pytest.mark.asyncio
async def test_active_work_row_registry_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6: rows carry cdp_url/chat_url/source from registry join."""
    from dataclasses import dataclass
    from pathlib import Path

    @dataclass(frozen=True)
    class _FakeReg:
        registration_id: str
        port: int
        profile_suffix: str
        profile: Path
        cdp_url: str
        holder: str
        purpose: str | None = None

    reg = _FakeReg(
        registration_id="reg-1",
        port=9223,
        profile_suffix="s",
        profile=Path("/tmp/p"),
        cdp_url="http://127.0.0.1:9223",
        holder="holder-a",
        purpose="operator-proxy",
    )
    chat = "https://claude.ai/cowork/cse_ac6"
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [reg])
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.chat_url_for_registration",
        lambda rid: chat if rid == "reg-1" else None,
    )
    store = ExecutionStore()
    record = await store.create(holder="test", purpose="operator-proxy")
    await store.set_registration_id(record.execution_id, "reg-1")
    snap = await store.active_work_snapshot()
    row = snap["rows"][0]
    assert row["cdp_url"] == "http://127.0.0.1:9223"
    assert row["chat_url"] == chat
    assert row["source"] == "cse-session-registry"
