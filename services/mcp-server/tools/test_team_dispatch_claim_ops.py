"""MCP team_dispatch schema and forward for house claim ops."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tools.frontier import register_frontier_tools


class _Recorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def _tool() -> Any:
    recorder = _Recorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["team_dispatch"]


def test_ops_enum_and_schema_contain_claim_ops() -> None:
    fn = _tool()
    sig = inspect.signature(fn)
    op = str(sig.parameters["op"].annotation)
    for name in ("claim", "claim_release", "claim_update", "occupancy", "registry"):
        assert name in op
    assert "seat_name" in sig.parameters
    assert "address" in sig.parameters
    assert "work_status" in sig.parameters
    assert "address_patch" in sig.parameters
    assert "work_key_prefix" in sig.parameters
    assert "claim_id" in sig.parameters
    assert "outcome" in sig.parameters
    assert "work_key" in sig.parameters
    assert "thread" in sig.parameters
    assert "ttl_s" in sig.parameters
    outcome = str(sig.parameters["outcome"].annotation)
    assert "completed" in outcome and "withdrawn" in outcome
    doc = fn.__doc__ or ""
    assert "409" in doc and "WORK_KEY_HELD" in doc
    assert "404" in doc and "CLAIM_NOT_FOUND" in doc
    assert "422" in doc


@pytest.mark.asyncio
async def test_claim_ops_forward_like_steer() -> None:
    fn = _tool()
    relay = AsyncMock(return_value={"claim_id": "claim-1", "status_code": 200})
    with patch("tools.frontier._relay", relay):
        result = await fn(
            op="claim",
            work_key="todo:house-state-primitive",
            thread="15945",
            caller_agent="cursor-sdk",
            ttl_s=120,
        )
        assert result["claim_id"] == "claim-1"
        body = relay.await_args.kwargs["body"]
        assert body["op"] == "claim"
        assert body["thread"] == "15945"
        assert body["caller_agent"] == "cursor-sdk"
        assert body["ttl_s"] == 120
        assert "holder_agent" not in body

        relay.reset_mock()
        await fn(
            op="claim_release",
            claim_id="claim-1",
            outcome="failed",
            caller_agent="cursor-sdk",
        )
        released = relay.await_args.kwargs["body"]
        assert released["op"] == "claim_release"
        assert released["outcome"] == "failed"

        relay.reset_mock()
        await fn(op="occupancy", work_key="todo:house-state-primitive")
        occ = relay.await_args.kwargs["body"]
        assert occ == {"op": "occupancy", "work_key": "todo:house-state-primitive"}

        relay.reset_mock()
        await fn(
            op="claim_update",
            claim_id="claim-1",
            work_status="landed",
            caller_agent="cursor-sdk",
        )
        upd = relay.await_args.kwargs["body"]
        assert upd["op"] == "claim_update"
        assert upd["work_status"] == "landed"

        relay.reset_mock()
        await fn(
            op="registry",
            work_key_prefix="todo:",
            window_hours=48,
            caller_agent="cursor-sdk",
        )
        reg = relay.await_args.kwargs["body"]
        assert reg["op"] == "registry"
        assert reg["work_key_prefix"] == "todo:"
        assert reg["window_hours"] == 48

        relay.reset_mock()
        await fn(
            op="claim",
            work_key="todo:house-state-primitive",
            thread="15945",
            caller_agent="cursor-sdk",
            seat_name="gc1",
            address={"kind": "grok_bot", "agent_id": "abc"},
            work_status="active",
        )
        with_addr = relay.await_args.kwargs["body"]
        assert with_addr["seat_name"] == "gc1"
        assert with_addr["address"]["agent_id"] == "abc"


@pytest.mark.asyncio
async def test_generate_forwards_generation_options_claim_id() -> None:
    fn = _tool()
    relay = AsyncMock(return_value={"dispatch_id": "d1"})
    with patch("tools.frontier._relay", relay):
        await fn(
            op="generate",
            seat="cursor-sdk",
            contract="freeform",
            lane="B",
            dispatch_thread_id="15945",
            prompt="implement the claim relay",
            model="cursor/grok-4.7",
            generation_options={"claim_id": "claim-house"},
            caller_agent="cursor-sdk",
        )
    body = relay.await_args.kwargs["body"]
    assert body["generation_options"]["claim_id"] == "claim-house"


@pytest.mark.asyncio
async def test_generate_forwards_top_level_claim_id_into_generation_options() -> None:
    fn = _tool()
    relay = AsyncMock(return_value={"dispatch_id": "d1"})
    with patch("tools.frontier._relay", relay):
        await fn(
            op="generate",
            seat="cursor-sdk",
            contract="freeform",
            lane="B",
            dispatch_thread_id="15945",
            prompt="successor admit",
            model="cursor/grok-4.7",
            claim_id="claim-top",
            caller_agent="cursor-sdk",
        )
    body = relay.await_args.kwargs["body"]
    assert body["generation_options"]["claim_id"] == "claim-top"


@pytest.mark.asyncio
async def test_generate_top_level_claim_id_does_not_override_generation_options() -> None:
    fn = _tool()
    relay = AsyncMock(return_value={"dispatch_id": "d1"})
    with patch("tools.frontier._relay", relay):
        await fn(
            op="generate",
            seat="cursor-sdk",
            contract="freeform",
            lane="B",
            dispatch_thread_id="15945",
            prompt="explicit wins",
            model="cursor/grok-4.7",
            claim_id="claim-top",
            generation_options={"claim_id": "claim-explicit"},
            caller_agent="cursor-sdk",
        )
    body = relay.await_args.kwargs["body"]
    assert body["generation_options"]["claim_id"] == "claim-explicit"
