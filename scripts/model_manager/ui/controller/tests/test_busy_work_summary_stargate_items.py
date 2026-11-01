"""Stargate active-work item clause in busy summaries."""

from __future__ import annotations

from scripts.model_manager.ui.controller.busy_work_summary import (
    format_active_work_summary,
)


def test_stargate_items_clause_keeps_active_count_prefix() -> None:
    payload = {
        "total": 2,
        "requests_in_flight": 2,
        "in_flight_items": [
            {
                "kind": "chat",
                "request_id": "abcd12345678",
                "routing_key": "anthropic/claude",
                "thread": "21597",
                "age_s": 12,
            }
        ],
    }
    summary = format_active_work_summary(payload)
    assert summary.startswith("active_count=2")
    assert "items=[" in summary
    assert "chat:abcd1234" in summary
    assert "thread=21597" in summary
    assert "age=12s" in summary


def test_stargate_async_pipeline_items_rendered() -> None:
    payload = {
        "total": 1,
        "async_pipelines_items": [
            {
                "kind": "pipeline_async",
                "execution_id": "exec-pipe-99",
                "pipeline": "team_dispatch",
                "started_at": "2026-11-01T12:00:00+00:00",
                "thread": "21609",
                "age_s": 45,
                "caller_agent": "cursor",
            }
        ],
    }
    summary = format_active_work_summary(payload)
    assert summary.startswith("active_count=1")
    assert "pipeline_async:exec-pip" in summary
    assert "team_dispatch" in summary
    assert "thread=21609" in summary
    assert "age=45s" in summary
