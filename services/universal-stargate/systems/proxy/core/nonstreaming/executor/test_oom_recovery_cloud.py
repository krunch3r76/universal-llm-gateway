"""Cloud 500 must not start VRAM eviction (19/827 idle class)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from model_id import ModelId

from systems.federation.common.types import FederatedGateway
from systems.proxy.core.nonstreaming.executor.oom_recovery import attempt_oom_recovery
from systems.routing.eviction.executor import execute_eviction_plan
from systems.routing.selection.decision.types import EvictionPlanSummary


def _cloud_gateway(*, loaded_count: int) -> FederatedGateway:
    target = ModelId.parse("anthropic/claude-sonnet-5")
    idle = [ModelId.parse(f"anthropic/idle-{i}") for i in range(loaded_count)]
    return FederatedGateway(
        gateway_id="cloud-anthropic",
        remote_stargate_id="cloud-anthropic",
        remote_stargate_url="http://127.0.0.1:9",
        backend_type="cloud_api",
        available_models=frozenset([target, *idle]),
        loaded_models=frozenset([target, *idle]),
    )


@pytest.mark.asyncio
async def test_attempt_oom_recovery_skips_is_cloud(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = _cloud_gateway(loaded_count=19)
    with patch(
        "systems.routing.eviction.executor.execute_eviction_plan",
        new_callable=AsyncMock,
    ) as evict:
        recovered = await attempt_oom_recovery(
            gateway=gateway,
            model_id=ModelId.parse("anthropic/claude-sonnet-5"),
            federated_manager=MagicMock(),
            federation_forwarder=MagicMock(),
            request_tracker=None,
            event_bus=None,
            request_id="req-cloud-500",
        )
    assert recovered is False
    evict.assert_not_called()
    assert "to free VRAM" not in caplog.text
    assert "OOM recovery skipped" in caplog.text


@pytest.mark.asyncio
async def test_execute_eviction_plan_refuses_is_cloud() -> None:
    victim = ModelId.parse("anthropic/claude-opus-4-5")
    gateway = FederatedGateway(
        gateway_id="cloud-openrouter",
        remote_stargate_id="cloud-openrouter",
        remote_stargate_url="http://127.0.0.1:9",
        backend_type="cloud_api",
        available_models=frozenset({victim}),
        loaded_models=frozenset({victim}),
    )
    forwarder = MagicMock()
    forwarder.forward_model_unload_request = AsyncMock()
    plan = EvictionPlanSummary(
        models_to_evict=frozenset({victim}),
        freed_vram_mb=0,
        freed_ram_mb=0,
        estimated_cost=0.0,
    )
    outcome = await execute_eviction_plan(
        forwarder=forwarder,
        federated_gateway=gateway,
        eviction_plan=plan,
        gateway_name=gateway.gateway_id,
    )
    assert outcome.ok is False
    assert outcome.reason == "cloud_gateway_has_no_vram"
    forwarder.forward_model_unload_request.assert_not_called()
