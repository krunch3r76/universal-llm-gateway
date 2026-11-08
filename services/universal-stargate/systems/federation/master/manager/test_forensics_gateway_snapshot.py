"""Forensics snapshot: skip VRAM overlay when RESOURCE_UPDATE already refreshed."""

from __future__ import annotations

import sys
from pathlib import Path

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.federation.master.manager.federated_gateway_manager import (  # noqa: E402
    FederatedGatewayManager,
)


def _manager_with_gateway(vram_free_mb: int) -> FederatedGatewayManager:
    mgr = FederatedGatewayManager.__new__(FederatedGatewayManager)
    mgr._event_bus = None
    mgr._load_resource_baseline = {}
    mgr._gateways = {
        "edge-jupiter-gateway": FederatedGateway(
            gateway_id="edge-jupiter-gateway",
            remote_stargate_id="relay-jupiter",
            remote_stargate_url="http://relay-jupiter:9998",
            node_id="jupiter",
            vram_free_mb=vram_free_mb,
            vram_total_mb=32_607,
            ram_free_mb=32_000,
            ram_total_mb=64_000,
            loaded_models=frozenset(
                {ModelId.parse("qwen3-14b-q4-k-m-40960")}
            ),
        )
    }
    return mgr


def test_merge_forensics_snapshot_updates_vram_not_model_sets() -> None:
    mgr = _manager_with_gateway(vram_free_mb=1_655)
    before_loaded = mgr._gateways["edge-jupiter-gateway"].loaded_models
    snapshot = {
        "resources": {
            "available_vram_mb": 11_980,
            "available_ram_mb": 32_000,
            "total_vram_mb": 32_607,
            "total_ram_mb": 64_000,
        },
        "loaded_models": ["qwen3-5-9b-q8-0-131072"],
        "loading_models": ["hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"],
        "busy_models": [],
    }
    mgr.apply_gateway_forensics_snapshot("edge-jupiter-gateway", snapshot)
    gw = mgr._gateways["edge-jupiter-gateway"]
    assert gw.vram_free_mb == 11_980
    assert gw.loaded_models == before_loaded
    assert gw.loading_models == frozenset()
    assert gw.busy_models == frozenset()


def test_stale_forensics_snapshot_skips_vram_after_resource_update() -> None:
    mgr = _manager_with_gateway(vram_free_mb=1_655)
    gw = mgr._gateways["edge-jupiter-gateway"]
    gw = mgr._replace_gateway(gw, telemetry_timestamp=100.0)
    mgr._gateways["edge-jupiter-gateway"] = gw
    rk = ModelId.parse(
        "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
    ).routing_key
    mgr.note_load_resource_baseline("edge-jupiter-gateway", rk)
    gw = mgr._replace_gateway(gw, telemetry_timestamp=200.0, vram_free_mb=25_000)
    mgr._gateways["edge-jupiter-gateway"] = gw
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
    assert updated.vram_free_mb == 25_000
    assert mid not in updated.loading_models


def test_apply_model_load_failed_applies_vram_when_no_fresh_resource_update() -> None:
    mgr = _manager_with_gateway(vram_free_mb=30_000)
    gw = mgr._gateways["edge-jupiter-gateway"]
    gw = mgr._replace_gateway(gw, telemetry_timestamp=100.0)
    rk = ModelId.parse(
        "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
    ).routing_key
    mgr.note_load_resource_baseline("edge-jupiter-gateway", rk)
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
