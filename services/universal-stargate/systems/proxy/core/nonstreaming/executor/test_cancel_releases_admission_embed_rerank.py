"""Cancel after admission and before federated_gateway releases the slot.

Callers are the embedding and rerank executors. Gateway selection can acquire
a CapacityPool token onto the request context and then raise CancelledError
before selected_gateway (and therefore federated_gateway) is assigned. Each
executor must still call release_capacity_token_fn from finally so in_flight
returns to the pre-acquire baseline. Reverting either production finally gate
fails the named test for that path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from model_id import ModelId

from systems.proxy.core.nonstreaming.executor.core import RequestExecutor
from systems.proxy.core.nonstreaming.executor.embeddings import (
    execute_embedding_request,
)
from systems.proxy.core.nonstreaming.executor.rerank import execute_rerank_request
from systems.routing.capacity.pool import CapacityPool

M = ModelId.parse("qwen3-5-9b-q8-0-131072")
GW = "edge-localhost-gateway"


@pytest.fixture
def pool() -> CapacityPool:
    capacity = CapacityPool(event_bus=None)
    capacity.set_capacity(GW, M.routing_key, 1)
    return capacity


def _release_fn() -> Any:
    executor = RequestExecutor.__new__(RequestExecutor)

    async def release(context: Any) -> None:
        await RequestExecutor._release_capacity_token(executor, context)

    return release


async def _assert_cancel_before_gateway_releases(
    pool: CapacityPool,
    *,
    request_id: str,
    execute: Any,
    forward_kw: str,
) -> None:
    _available, in_flight_before, _cap = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_before == 0

    async def select_gateway_fn(context: Any) -> None:
        token = await pool.acquire_token(
            request_id=request_id,
            model_id=M.routing_key,
            allowed_gateway_ids=frozenset({GW}),
        )
        context.capacity_token = token
        assert context.federated_gateway is None
        raise asyncio.CancelledError()

    async def forward_fn(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("forward must not run after cancel during select")

    kwargs = {
        "model_id": str(M),
        "request_body": {"model": str(M), "input": "x"},
        "request_id": request_id,
        "select_gateway_fn": select_gateway_fn,
        "release_routing_key_fn": lambda _request_id: None,
        "release_capacity_token_fn": _release_fn(),
        forward_kw: forward_fn,
        "event_bus": None,
    }
    with pytest.raises(asyncio.CancelledError):
        await execute(**kwargs)

    _available_after, in_flight_after, _cap_after = pool.get_slot_info(
        GW, M.routing_key
    )
    assert in_flight_after == in_flight_before


@pytest.mark.asyncio
async def test_embed_cancel_before_federated_gateway_releases_in_flight(
    pool: CapacityPool,
) -> None:
    await _assert_cancel_before_gateway_releases(
        pool,
        request_id="req-embed-cancel",
        execute=execute_embedding_request,
        forward_kw="forward_embedding_fn",
    )


@pytest.mark.asyncio
async def test_rerank_cancel_before_federated_gateway_releases_in_flight(
    pool: CapacityPool,
) -> None:
    await _assert_cancel_before_gateway_releases(
        pool,
        request_id="req-rerank-cancel",
        execute=execute_rerank_request,
        forward_kw="forward_rerank_fn",
    )
