import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.federation.master.orchestration.config import (  # noqa: E402
    OrchestrationConfig,
)
from systems.federation.master.orchestration.load_orchestrator import (  # noqa: E402
    FederatedLoadOrchestrator,
)


@dataclass(frozen=True)
class _FakeModelId:
    routing_key: str

    def __str__(self) -> str:
        return self.routing_key


class _FastTimeoutForwarder:
    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        raise TimeoutError("edge admission timed out after 5.0s")


class _SlowForwarder:
    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"status": "ok"}


def _gateway() -> SimpleNamespace:
    return SimpleNamespace(
        gateway_id="edge-jupiter-gateway",
        remote_stargate_id="edge-jupiter",
        remote_stargate_url="http://edge-jupiter",
        is_cloud=False,
    )


@pytest.mark.asyncio
async def test_inner_timeout_reports_actual_fast_failure() -> None:
    orchestrator = FederatedLoadOrchestrator(
        _FastTimeoutForwarder(),
        config=OrchestrationConfig(load_retry_count=0),
    )

    with pytest.raises(HTTPException) as excinfo:
        await orchestrator.ensure_model_loaded_on_remote(
            _gateway(),
            _FakeModelId("hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid"),
        )

    detail = excinfo.value.detail
    assert detail["code"] == "LOAD_TIMEOUT"
    assert "before the 180s master timeout budget expired" in detail["message"]
    assert "edge admission timed out after 5.0s" in detail["message"]
    assert "after 180s" not in detail["message"]
    assert detail["data"]["timeout_budget_s"] == 180
    assert detail["data"]["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_wall_clock_timeout_reports_elapsed_and_budget() -> None:
    orchestrator = FederatedLoadOrchestrator(
        _SlowForwarder(),
        config=OrchestrationConfig(
            load_timeout=0.01,
            coalesce_wait_timeout=30.01,
            load_retry_count=0,
        ),
    )

    with pytest.raises(HTTPException) as excinfo:
        await orchestrator.ensure_model_loaded_on_remote(
            _gateway(),
            _FakeModelId("hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid"),
        )

    detail = excinfo.value.detail
    assert detail["code"] == "LOAD_TIMEOUT"
    assert "Timeout loading" in detail["message"]
    assert "(budget 0.01s)" in detail["message"]
    assert detail["data"]["timeout_budget_s"] == 0.01
    assert detail["data"]["elapsed_ms"] > 0
