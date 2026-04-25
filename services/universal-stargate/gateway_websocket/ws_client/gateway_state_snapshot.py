"""Build a master-side snapshot of GatewayState for forensics events.

Snapshots the Stargate WebSocket client's *cached* projection of a gateway:
loaded/busy/loading models, per-model VRAM/RAM details, aggregate resource
availability, and reservation ledger. Attached to model.load.failed for
oncall debugging.

This is a master-side, lagging view (telemetry-driven). It complements the
edge-side worker_snapshot (real process tree + live hardware metrics)
captured on the gateway at the actual moment of failure.

All capture is best-effort — exceptions degrade to None or partial fields and
must never block emission.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .state import GatewayState

logger = get_logger(__name__)


def build_gateway_state_snapshot(
    state: GatewayState,
    gateway_name: str,
    gateway_url: str,
) -> dict[str, Any] | None:
    """Snapshot the cached GatewayState for inclusion on model.load.failed.

    Returns a JSON-serializable dict, or None on catastrophic capture failure.
    """
    try:
        return {
            "gateway_name": gateway_name,
            "gateway_url": gateway_url,
            "captured_at": time.time(),
            "loaded_models": sorted(state.loaded_models),
            "loading_models": sorted(state.loading_models),
            "busy_models": sorted(state.busy_models),
            "model_details": _serialize_model_details(state.model_details),
            "measured_model_vram_mb": dict(state.measured_model_vram),
            "resources": _serialize_resources(state),
        }
    except Exception as e:
        logger.warning(
            "gateway_state_snapshot build failed for %s: %s",
            gateway_name,
            e,
            exc_info=True,
        )
        return None


def _serialize_model_details(
    model_details: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Defensive shallow copy + scalar coercion for nested model details."""
    out: dict[str, dict[str, Any]] = {}
    for model_id, details in model_details.items():
        if not isinstance(details, dict):
            continue
        out[model_id] = {
            k: v
            for k, v in details.items()
            if isinstance(v, (str, int, float, bool)) or v is None
        }
    return out


def _serialize_resources(state: GatewayState) -> dict[str, Any]:
    res = state.resources
    return {
        "total_vram_mb": res.total_vram_mb,
        "available_vram_mb": res.available_vram_mb,
        "total_ram_mb": res.total_ram_mb,
        "available_ram_mb": res.available_ram_mb,
    }
