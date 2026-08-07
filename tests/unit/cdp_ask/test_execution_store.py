"""Unit tests for cdp_ask.execution_store TTL, reaper, and boot reconcile."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import patch

import pytest

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.stop_ack_checkin import STOP_ACK_QUIET_S, is_stop_ack_candidate


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

    def _deregister(rid: str, **kwargs: object) -> None:
        deregistered.append(rid)

    fake_active = {
        "orphan-1": {
            "registration_id": "orphan-1",
            "port": 9229,
            "profile_suffix": "reg-orphan01",
            "holder": "a",
            "status": "active",
        }
    }

    with patch("claude_bundles.cdp_registry._load_active", return_value=fake_active):
        with patch("claude_bundles.cdp_registry.deregister_lane", side_effect=_deregister):
            reaped = await fast_store.boot_reconcile()

    assert reaped == ["orphan-1"]
    assert deregistered == ["orphan-1"]


@pytest.mark.asyncio
async def test_boot_reconcile_keeps_live_execution_lane(
    fast_store: ExecutionStore,
) -> None:
    deregistered: list[str] = []
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.set_registration_id(record.execution_id, "live-1")
    fake_active = {
        "live-1": {
            "registration_id": "live-1",
            "port": 9229,
            "profile_suffix": "reg-live01",
            "holder": "a",
            "status": "active",
        }
    }

    with patch("claude_bundles.cdp_registry._load_active", return_value=fake_active):
        with patch(
            "claude_bundles.cdp_registry.deregister_lane",
            side_effect=lambda rid, **kw: deregistered.append(rid),
        ):
            reaped = await fast_store.boot_reconcile()

    assert reaped == []
    assert deregistered == []


@pytest.mark.asyncio
async def test_execution_ttl_reaper_marks_failed(fast_store: ExecutionStore) -> None:
    deregistered: list[str] = []
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.set_registration_id(record.execution_id, "lane-1")
    await fast_store.attach_task(record.execution_id, asyncio.create_task(asyncio.sleep(60)))

    with patch(
        "claude_bundles.cdp_registry.deregister_lane",
        side_effect=lambda rid, **kw: deregistered.append(rid),
    ):
        await asyncio.sleep(0.12)
        await fast_store._reap_once()

    updated = await fast_store.get(record.execution_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "execution TTL exceeded"
    assert deregistered == ["lane-1"]


@pytest.mark.asyncio
async def test_ttl_kills_op_while_cse_alive(fast_store: ExecutionStore) -> None:
    """Named for the 6893 failure: OP purpose must survive execution TTL.

    Pre-fix, age > execution_ttl_s cancelled operator-proxy while CSE glass
    stayed up. Post-fix, reaper skips OP/mission purposes.
    """
    deregistered: list[str] = []
    record = await fast_store.create(holder="seat", purpose="operator-proxy")
    await fast_store.set_registration_id(record.execution_id, "op-lane-1")
    task = asyncio.create_task(asyncio.sleep(60))
    await fast_store.attach_task(record.execution_id, task)

    with patch(
        "claude_bundles.cdp_registry.deregister_lane",
        side_effect=lambda rid, **kw: deregistered.append(rid),
    ):
        await asyncio.sleep(0.12)
        await fast_store._reap_once()

    updated = await fast_store.get(record.execution_id)
    assert updated is not None
    assert updated.status == "running"
    assert updated.error is None
    assert deregistered == []
    assert not task.done()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_idle_reaper_drops_terminal_records(fast_store: ExecutionStore) -> None:
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.mark_terminal(record.execution_id, status="completed", result={"ok": True})

    await asyncio.sleep(0.12)
    await fast_store._reap_once()

    assert await fast_store.get(record.execution_id) is None


@pytest.mark.asyncio
async def test_iter_stop_ack_candidates_excludes_awaiting_wake(
    fast_store: ExecutionStore,
) -> None:
    record = await fast_store.create(holder="seat", purpose="operator-proxy")
    await fast_store.set_registration_id(record.execution_id, "reg-m")
    await fast_store.update_liveness(
        record.execution_id,
        streaming=False,
        stop=True,
        tool_pause=False,
        liveness_observed_at=100.0,
    )
    await fast_store.mark_awaiting_wake(record.execution_id, result={"ok": True})

    now = 100.0 + STOP_ACK_QUIET_S + 10.0
    candidates = await fast_store.iter_stop_ack_candidates(now)
    assert candidates == []


@pytest.mark.asyncio
async def test_iter_stop_ack_candidates_includes_mission_stopped(
    fast_store: ExecutionStore,
) -> None:
    record = await fast_store.create(holder="seat", purpose="mission")
    await fast_store.set_registration_id(record.execution_id, "reg-m2")
    await fast_store.attach_task(record.execution_id, asyncio.create_task(asyncio.sleep(60)))
    await fast_store.update_liveness(
        record.execution_id,
        streaming=False,
        stop=True,
        tool_pause=False,
        liveness_observed_at=50.0,
    )

    now = 50.0 + STOP_ACK_QUIET_S + 1.0
    candidates = await fast_store.iter_stop_ack_candidates(now)
    assert len(candidates) == 1
    assert candidates[0].execution_id == record.execution_id
    assert is_stop_ack_candidate(candidates[0], now) is True


@pytest.mark.asyncio
async def test_iter_stop_ack_candidates_excludes_ask(fast_store: ExecutionStore) -> None:
    record = await fast_store.create(holder="seat", purpose="ask")
    await fast_store.update_liveness(
        record.execution_id,
        streaming=False,
        stop=True,
        tool_pause=False,
        liveness_observed_at=10.0,
    )
    candidates = await fast_store.iter_stop_ack_candidates(1000.0)
    assert candidates == []
