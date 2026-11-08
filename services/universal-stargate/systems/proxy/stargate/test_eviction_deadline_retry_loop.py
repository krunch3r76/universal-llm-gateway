"""Eviction wait budget arming via execute_with_retry (review B1)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request
from starlette.responses import Response

import systems.proxy.stargate.requests.retry as request_retry
from systems.proxy.core.nonstreaming.federated_routing import (
    wait_continuation as wait_continuation_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (
    arm_or_remaining_eviction_budget,
    get_eviction_wait_budget_s,
)
from systems.proxy.stargate.requests.retry import execute_with_retry


def test_missing_routing_config_defaults_eviction_budget_to_300(caplog) -> None:
    with caplog.at_level("ERROR"):
        assert get_eviction_wait_budget_s(None) == 300.0
    assert sum(1 for r in caplog.records if r.levelname == "ERROR") == 1


@pytest.mark.asyncio
async def test_execute_with_retry_defers_eviction_arm_past_queue_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mono = {"t": 1000.0}

    def fake_monotonic() -> float:
        return mono["t"]

    monkeypatch.setattr(request_retry.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(wait_continuation_mod.time, "monotonic", fake_monotonic)

    clamped: list[float] = []

    async def fake_execute(context: SimpleNamespace) -> Response:
        assert context._eviction_wait_deadline_mono is None
        mono["t"] += 400.0
        clamped.append(arm_or_remaining_eviction_budget(context, 300.0))
        return Response(content=b"ok", status_code=200)

    context = SimpleNamespace(
        request_id="req-evict-arm",
        target_gateway_id="gw-1",
        _eviction_wait_deadline_mono=None,
        _capacity_deadline_mono=None,
        queue_wait_timeout_s=None,
        request_timeout_hint=None,
        selected_model=None,
        excluded_gateway_ids=set(),
        excluded_gateway_errors={},
        routing_endpoint_category=None,
    )
    proxy = SimpleNamespace(
        config=MagicMock(
            get_request_queue_config=MagicMock(
                return_value={"queue_timeout": 1800.0, "upstream_retry_timeout": 120.0}
            )
        ),
        routing_config={},
        request_executor=SimpleNamespace(execute_request=fake_execute),
        event_bus=None,
        gateway_url="http://localhost",
        stability_tracker=None,
    )
    request = AsyncMock(spec=Request)
    request.is_disconnected = AsyncMock(return_value=False)

    response = await execute_with_retry(
        proxy,
        context,
        "test-model",
        request,
        time.time(),
    )
    assert response.status_code == 200
    assert clamped == [pytest.approx(300.0)]


@pytest.mark.asyncio
async def test_failover_retry_does_not_rearm_eviction_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mono = {"t": 1000.0}

    def fake_monotonic() -> float:
        return mono["t"]

    monkeypatch.setattr(request_retry.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(wait_continuation_mod.time, "monotonic", fake_monotonic)
    attempts = {"n": 0}
    clamped: list[float] = []

    async def fake_execute(context: SimpleNamespace) -> Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            clamped.append(arm_or_remaining_eviction_budget(context, 300.0))
            mono["t"] += 100.0
            clamped.append(arm_or_remaining_eviction_budget(context, 300.0))
            from fastapi import HTTPException
            from universal_protocol import ErrorCode, error_envelope

            raise HTTPException(
                status_code=502,
                detail=error_envelope(
                    code=ErrorCode.RESOURCE_UNAVAILABLE,
                    message="upstream",
                    source="gateway",
                    retryable=True,
                    data={"gateway_id": "gw-a"},
                ),
            )
        clamped.append(arm_or_remaining_eviction_budget(context, 300.0))
        return Response(content=b"ok", status_code=200)

    deadline_at_start = 1000.0 + 300.0
    context = SimpleNamespace(
        request_id="req-failover",
        target_gateway_id="gw-1",
        _eviction_wait_deadline_mono=None,
        _capacity_deadline_mono=None,
        queue_wait_timeout_s=None,
        request_timeout_hint=None,
        selected_model="m",
        excluded_gateway_ids=set(),
        excluded_gateway_errors={},
        routing_endpoint_category=None,
    )
    proxy = SimpleNamespace(
        config=MagicMock(
            get_request_queue_config=MagicMock(
                return_value={"queue_timeout": 1800.0, "upstream_retry_timeout": 120.0}
            )
        ),
        routing_config={},
        request_executor=SimpleNamespace(execute_request=fake_execute),
        event_bus=None,
        gateway_url="http://localhost",
        stability_tracker=SimpleNamespace(clear_binding=lambda _m: None),
    )
    request = AsyncMock(spec=Request)
    request.is_disconnected = AsyncMock(return_value=False)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    await execute_with_retry(
        proxy,
        context,
        "test-model",
        request,
        time.time(),
    )
    assert clamped[0] == pytest.approx(300.0)
    assert clamped[1] == pytest.approx(200.0)
    assert clamped[2] == pytest.approx(200.0)
    assert context._eviction_wait_deadline_mono == deadline_at_start
