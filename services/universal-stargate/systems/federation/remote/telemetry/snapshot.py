"""
Telemetry snapshot building and sending.

Consolidates payload construction for initial, periodic, and reconnect snapshots.

INVARIANT: ∀ snapshot: payload ⊇ {url, total_ram_mb, available_ram_mb,
           total_vram_mb, available_vram_mb, loaded_models, busy_models,
           available_models}

INVARIANT: apply_filtering=True for initial/reconnect/resource_update
           apply_filtering=False for periodic
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateway_websocket import GatewayWebSocketClient

logger = get_logger(__name__)


class ResourceInfo(Protocol):
    """Protocol for gateway resource information."""

    total_ram_mb: int
    available_ram_mb: int
    total_vram_mb: int
    available_vram_mb: int


def apply_activation_filtering(
    ws_client: GatewayWebSocketClient,
    model_ids: set[str],
    resources: ResourceInfo,
) -> set[str]:
    """
    Filter models based on hardware activation state.

    Semantics (from gateways/filtering/activation.py):
    - Empty activated_contexts → returns model_ids unfiltered
    - Empty activated list ([]) → auto-selects highest fitting contexts
    - Non-synthetic IDs → included as-is
    - Full-GPU activation → includes hybrid variant if present

    Args:
        ws_client: Gateway WebSocket client for catalog access
        model_ids: Raw model IDs to filter
        resources: Gateway resource info (RAM/VRAM totals)

    Returns:
        Filtered set of model IDs that can run on current hardware
    """
    from gateways.filtering import ActivationInfo, filter_by_activation

    # Get activated contexts from catalog
    # NOTE: Fail-fast if catalog is None (gateway bug)
    catalog = ws_client.get_catalog()
    raw_activated = catalog.get("activated_contexts", {})

    # Early return: no activation rules = no filtering
    if not raw_activated:
        return model_ids

    # Convert to ActivationInfo objects
    activated_contexts: dict[str, ActivationInfo] = {}
    for model_id, contexts_data in raw_activated.items():
        activated_contexts[model_id] = ActivationInfo(
            cpu=contexts_data.get("cpu"),
            gpu=contexts_data.get("gpu"),
        )

    # Build gateway resources dict
    gateway_resources = {
        "local": {
            "total_ram_mb": resources.total_ram_mb,
            "total_vram_mb": resources.total_vram_mb,
        }
    }

    # Filter models (empty profile resources = basic filtering only)
    # model_profile_resources not available from gateway yet
    model_profile_resources: dict[str, dict[str, dict[int, dict[str, int]]]] = {}

    filtered = filter_by_activation(
        model_ids,
        activated_contexts,
        model_profile_resources,
        gateway_resources,
    )

    logger.debug(f"🔍 Activation filtering: {len(model_ids)} → {len(filtered)} models")

    return filtered


def build_telemetry_payload(
    ws_client: GatewayWebSocketClient,
    apply_filtering: bool = True,
) -> dict[str, Any]:
    """
    Build complete telemetry payload from current gateway state.

    Args:
        ws_client: Gateway WebSocket client (MUST be connected)
        apply_filtering: Whether to apply activation filtering.
                        True for initial/reconnect/resource_update.
                        False for periodic (freshness ping only).

    Returns:
        Telemetry payload dict ready for RESOURCE_UPDATE signal

    Includes:
        - Resource metrics (RAM/VRAM)
        - Model lifecycle state (loaded, busy, available)
        - Model resource requirements (vram_usage, ram_usage) for routing
    """
    resources = ws_client.get_resources()
    loaded_models = ws_client.get_loaded_models()
    busy_models = ws_client.get_busy_models()
    raw_available_models = ws_client.get_models()

    if apply_filtering:
        available_models = apply_activation_filtering(
            ws_client, raw_available_models, resources
        )
    else:
        available_models = raw_available_models

    return {
        "total_ram_mb": resources.total_ram_mb,
        "available_ram_mb": resources.available_ram_mb,
        "total_vram_mb": resources.total_vram_mb,
        "available_vram_mb": resources.available_vram_mb,
        "loaded_models": list(loaded_models),
        "busy_models": list(busy_models),
        "available_models": list(available_models),
    }


def log_snapshot_sent(
    context: str,
    available_count: int,
    raw_count: int,
    loaded_count: int,
    busy_count: int,
) -> None:
    """Log telemetry snapshot sent with filtering stats."""
    logger.info(
        f"📤 {context}: {available_count} available "
        f"({raw_count} pre-filter), "
        f"{loaded_count} loaded, {busy_count} busy"
    )
