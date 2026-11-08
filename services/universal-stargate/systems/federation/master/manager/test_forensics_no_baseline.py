"""Forensics VRAM overlay requires an orchestrator-recorded load baseline."""

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


def test_apply_forensics_without_baseline_leaves_vram_unchanged() -> None:
    mgr = FederatedGatewayManager.__new__(FederatedGatewayManager)
    mgr._event_bus = None
    mgr._load_resource_baseline = {}
    mgr._gateways = {
        "edge-jupiter-gateway": FederatedGateway(
            gateway_id="edge-jupiter-gateway",
            remote_stargate_id="relay-jupiter",
            remote_stargate_url="http://relay-jupiter:9998",
            node_id="jupiter",
            vram_free_mb=25_000,
            vram_total_mb=32_607,
        )
    }
    mgr.apply_gateway_forensics_snapshot(
        "edge-jupiter-gateway",
        {"resources": {"available_vram_mb": 11_980}},
        routing_key="qwen3-5-9b-q8-0-131072",
    )
    assert mgr._gateways["edge-jupiter-gateway"].vram_free_mb == 25_000
