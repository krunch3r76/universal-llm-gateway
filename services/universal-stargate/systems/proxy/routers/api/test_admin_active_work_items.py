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
