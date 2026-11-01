"""Contextvar scope and route classification for in-flight meta."""

from __future__ import annotations

import asyncio

import pytest

from src.core.gateway.in_flight_requests import InFlightRequestTracker
from src.core.gateway.request_inflight_meta import (
    bind_request_inflight_meta,
    current_request_inflight_meta,
    infer_inflight_kind,
    inflight_meta_scope,
)


@pytest.fixture
def reset_inflight_meta():
    bind_request_inflight_meta(None)
    yield
    bind_request_inflight_meta(None)


def test_inflight_meta_scope_resets_on_exit(reset_inflight_meta) -> None:
    with inflight_meta_scope({"kind": "chat", "thread": "first"}):
        assert current_request_inflight_meta() is not None
        assert current_request_inflight_meta()["thread"] == "first"
    assert current_request_inflight_meta() is None


def test_cross_request_meta_isolation_after_scope(reset_inflight_meta) -> None:
    tracker = InFlightRequestTracker()
    with inflight_meta_scope({"kind": "chat", "thread": "21597"}):
        tracker.track_request("gw", "req-one", "model/a", meta=None)
    tracker.track_request("gw", "req-two", "model/b", meta=None)
    by_id = {row["request_id"]: row for row in tracker.list_in_flight(limit=32)}
    assert by_id["req-one"]["thread"] == "21597"
    assert by_id["req-two"]["kind"] == "unknown"
    assert "thread" not in by_id["req-two"]


@pytest.mark.asyncio
async def test_task_after_scope_exit_does_not_inherit_meta(reset_inflight_meta) -> None:
    seen: dict[str, object | None] = {"meta": "unset"}

    async def observe() -> None:
        await asyncio.sleep(0)
        seen["meta"] = current_request_inflight_meta()

    with inflight_meta_scope({"kind": "pipeline_sync", "thread": "prior"}):
        pass
    await asyncio.create_task(observe())
    assert seen["meta"] is None


def test_infer_inflight_kind_cdp_ask_route_prefix_only() -> None:
    assert (
        infer_inflight_kind(
            is_pipeline_internal=False,
            is_pipeline_model=False,
            client_wants_stream=False,
            model_id="anthropic/claude",
            request_path="/api/v1/providers/cdp/ask",
        )
        == "cdp_ask"
    )
    assert (
        infer_inflight_kind(
            is_pipeline_internal=False,
            is_pipeline_model=False,
            client_wants_stream=False,
            model_id="anthropic/claude",
            request_path="/v1/chat/completions/cdp-backdoor",
        )
        == "cloud"
    )

