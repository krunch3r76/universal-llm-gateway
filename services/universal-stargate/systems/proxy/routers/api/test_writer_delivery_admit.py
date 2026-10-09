"""Admit refuses writer-specialist body delivery before a run is registered."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from systems.proxy.routers.api.pipelines_dispatch import (
    DispatchRequest,
    ResultDeliveryConfig,
    admit_dispatch,
)


def _request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/pipelines/dispatch",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


def _proxy(delivery: str | None) -> MagicMock:
    pipeline = SimpleNamespace(model_extra={"delivery": delivery} if delivery else {})
    proxy = MagicMock()
    proxy.is_pipeline_system_ready = True
    proxy.pipeline_registry.is_pipeline.return_value = True
    proxy.pipeline_registry.get_pipeline.return_value = pipeline
    proxy.pipeline_executor.generate_execution_id.return_value = "exec-1"
    proxy.pipeline_dispatch_tracker = MagicMock()
    proxy.request_preparer.prepare_request = AsyncMock()
    return proxy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", ["writer-specialist-v1", "writing/writer-specialist-v1"]
)
@pytest.mark.parametrize(
    "body",
    [
        {
            "result_delivery": ResultDeliveryConfig(
                bus_thread="1", bus_from_agent="a", bus_to_agent="b"
            )
        },
        {"output_contract": "thread", "target_thread": "9"},
        {"op": "to_thread", "target_thread": "9", "output_contract": "thread"},
        {
            "result_delivery": ResultDeliveryConfig(
                bus_thread="1",
                bus_from_agent="a",
                bus_to_agent="b",
                bus_brief_summary="draft body",
            )
        },
    ],
)
async def test_writer_delivery_shapes_are_422(model: str, body: dict) -> None:
    proxy = _proxy("id_status_only")
    dispatch = DispatchRequest(model=model, messages=[], **body)
    response = await admit_dispatch(_request(), proxy, dispatch)
    assert response.status_code == 422
    payload = response.body
    assert b"writer_delivery_refused" in payload
    proxy.pipeline_dispatch_tracker.register_execution.assert_not_called()


@pytest.mark.asyncio
async def test_other_pipeline_delivery_still_registers() -> None:
    proxy = _proxy(None)
    proxy.request_preparer.prepare_request = AsyncMock(side_effect=RuntimeError("stop"))
    dispatch = DispatchRequest(
        model="other-pipe",
        messages=[{"role": "user", "content": "hi"}],
        result_delivery=ResultDeliveryConfig(
            bus_thread="1", bus_from_agent="a", bus_to_agent="b"
        ),
    )
    response = await admit_dispatch(_request(), proxy, dispatch)
    assert response.status_code != 422
    proxy.pipeline_dispatch_tracker.register_execution.assert_called_once()
