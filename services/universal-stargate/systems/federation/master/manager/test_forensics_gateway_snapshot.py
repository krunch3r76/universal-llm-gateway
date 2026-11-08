"""Forensics snapshot must refresh master VRAM view after edge load refusal."""

from __future__ import annotations

import sys
from pathlib import Path

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.federation.master.manager.federated_gateway_manager import (  # noqa: E402
    FederatedGatewayManager,
)


def _manager_with_gateway(vram_free_mb: int) -> FederatedGatewayManager:
    mgr = FederatedGatewayManager.__new__(FederatedGatewayManager)
    mgr._event_bus = None
    mgr._gateways = {
        "edge-jupiter-gateway": FederatedGateway(
            gateway_id="edge-jupiter-gateway",
            remote_stargate_id="edge-jupiter",
            remote_stargate_url="http://edge:9998",
            node_id="jupiter",
            vram_free_mb=vram_free_mb,
            vram_total_mb=32_607,
            ram_free_mb=32_000,
            ram_total_mb=64_000,
        )
    }
    return mgr


def test_merge_forensics_snapshot_updates_vram_free() -> None:
    mgr = _manager_with_gateway(vram_free_mb=1_655)
    snapshot = {
        "resources": {
            "available_vram_mb": 11_980,
            "available_ram_mb": 32_000,
            "total_vram_mb": 32_607,
            "total_ram_mb": 64_000,
        },
        "loaded_models": ["qwen3-5-9b-q8-0-131072"],
    }
    mgr.apply_gateway_forensics_snapshot("edge-jupiter-gateway", snapshot)
    gw = mgr._gateways["edge-jupiter-gateway"]
    assert gw.vram_free_mb == 11_980
    assert len(gw.loaded_models) == 1


def test_apply_model_load_failed_path_uses_inline_snapshot() -> None:
    from model_id import ModelId

    mgr = _manager_with_gateway(vram_free_mb=30_000)
    gw = mgr._gateways["edge-jupiter-gateway"]
    mid = ModelId.parse("hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid")
    gw = mgr._replace_gateway(gw, loading_models=frozenset({mid}))
    updated = mgr._apply_model_load_failed(
        gw,
        {
            "model_id": mid,
            "error": "VRAM: need 30184MB, have 11980MB",
            "gateway_state_snapshot": {
                "resources": {"available_vram_mb": 11_980},
            },
        },
    )
    assert updated.vram_free_mb == 11_980
    assert mid not in updated.loading_models
