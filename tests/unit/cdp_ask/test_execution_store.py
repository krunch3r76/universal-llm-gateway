"""Unit tests for cdp_ask.execution_store TTL, reaper, and boot reconcile."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from cdp_ask.execution_store import ExecutionStore


@pytest.fixture
def fast_store() -> ExecutionStore:
    return ExecutionStore(
        execution_ttl_s=0.05,
        idle_ttl_s=0.05,
        reaper_interval_s=0.02,
    )


@pytest.mark.asyncio
async def test_boot_reconcile_reaps_orphaned_lanes(fast_store: ExecutionStore) -> None:
    deregistered: list[str] = []
    fast_store.bind_deregister(lambda rid: deregistered.append(rid))
    fake_reg = MagicMock(registration_id="orphan-1")

    with patch("claude_bundles.cdp_registry.list_active", return_value=[fake_reg]):
        reaped = await fast_store.boot_reconcile()

    assert reaped == ["orphan-1"]
    assert deregistered == ["orphan-1"]


@pytest.mark.asyncio
async def test_boot_reconcile_keeps_live_execution_lane(
    fast_store: ExecutionStore,
) -> None:
    deregistered: list[str] = []
    fast_store.bind_deregister(lambda rid: deregistered.append(rid))
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.set_registration_id(record.execution_id, "live-1")
    fake_reg = MagicMock(registration_id="live-1")

    with patch("claude_bundles.cdp_registry.list_active", return_value=[fake_reg]):
        reaped = await fast_store.boot_reconcile()

    assert reaped == []
    assert deregistered == []


@pytest.mark.asyncio
async def test_execution_ttl_reaper_marks_failed(fast_store: ExecutionStore) -> None:
    deregistered: list[str] = []
    fast_store.bind_deregister(lambda rid: deregistered.append(rid))
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.set_registration_id(record.execution_id, "lane-1")
    await fast_store.attach_task(record.execution_id, asyncio.create_task(asyncio.sleep(60)))

    await asyncio.sleep(0.12)
    await fast_store._reap_once()

    updated = await fast_store.get(record.execution_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "execution TTL exceeded"
    assert deregistered == ["lane-1"]


@pytest.mark.asyncio
async def test_idle_reaper_drops_terminal_records(fast_store: ExecutionStore) -> None:
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.mark_terminal(record.execution_id, status="completed", result={"ok": True})

    await asyncio.sleep(0.12)
    await fast_store._reap_once()

    assert await fast_store.get(record.execution_id) is None
