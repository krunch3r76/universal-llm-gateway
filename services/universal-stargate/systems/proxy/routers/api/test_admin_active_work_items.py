"""Admin active-work exposes in-flight item rows without changing counts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from systems.proxy.routers.api import admin_active_work as mod


@pytest.mark.asyncio
async def test_get_active_work_includes_items_and_preserves_counts() -> None:
    proxy = MagicMock()
    proxy.pipeline_dispatch_tracker = None
    sample_items = [
        {
            "request_id": "abcd1234-5678",
            "gateway_id": "localhost",
            "routing_key": "llama",
            "kind": "chat",
            "thread": "21597",
            "started_at": "2026-11-01T12:00:00+00:00",
            "age_s": 3.0,
        }
    ]
    with (
        patch.object(mod.in_flight_tracker, "get_total_in_flight", return_value=2),
        patch.object(
            mod.in_flight_tracker, "list_in_flight", return_value=sample_items
        ),
        patch(
            "systems.frontier_consult.cdp_generate_inflight_ledger.list_open_inflight_legs",
            return_value=[],
        ),
    ):
        resp = await mod.get_active_work(proxy=proxy, _current_user={"sub": "test"})
    import json

    content = json.loads(resp.body)
    assert content["requests_in_flight"] == 2
    assert content["total"] == 2
    assert content["busy"] is True
    assert content["in_flight_items"] == sample_items
    assert content["async_pipelines_items"] == []


@pytest.mark.asyncio
async def test_get_active_work_async_pipeline_items_fields_and_cap() -> None:
    from types import SimpleNamespace

    proxy = MagicMock()
    now_mono = __import__("time").monotonic()
    records = {}
    for idx in range(40):
        exec_id = f"exec-{idx:02d}"
        record = SimpleNamespace(
            execution_id=exec_id,
            pipeline="team_dispatch",
            status="running" if idx >= 8 else "completed",
            started_at=f"2026-11-01T12:{idx:02d}:00+00:00",
            started_at_monotonic=now_mono - float(40 - idx),
            caller_agent=None,
            dispatch_thread_id="21609" if idx == 39 else None,
        )
        records[exec_id] = record

    tracker = MagicMock()
    tracker.records = records
    proxy.pipeline_dispatch_tracker = tracker

    with (
        patch.object(mod.in_flight_tracker, "get_total_in_flight", return_value=0),
        patch.object(mod.in_flight_tracker, "list_in_flight", return_value=[]),
        patch(
            "systems.frontier_consult.cdp_generate_inflight_ledger.list_open_inflight_legs",
            return_value=[],
        ),
    ):
        resp = await mod.get_active_work(proxy=proxy, _current_user={"sub": "test"})

    import json

    content = json.loads(resp.body)
    assert content["async_pipelines_running"] == 32
    items = content["async_pipelines_items"]
    assert len(items) == 32
    assert items[0]["execution_id"] == "exec-08"
    assert items[-1]["execution_id"] == "exec-39"
    assert all(item["kind"] == "pipeline_async" for item in items)
    assert all("started_at" in item and "age_s" in item for item in items)
    assert items[-1]["thread"] == "21609"
