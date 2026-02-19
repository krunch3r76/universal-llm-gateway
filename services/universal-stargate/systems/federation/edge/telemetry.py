"""
Initial telemetry helpers for Edge mode.

Extracted from server.py for SLOC compliance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger
from universal_protocol.messages import TelemetrySource

from ..common.protocol import FederationMessageType

if TYPE_CHECKING:
    from gateway_websocket import GatewayWebSocketClient

logger = get_logger(__name__)


def build_initial_telemetry_payload(
    ws_client: GatewayWebSocketClient,
    source: TelemetrySource,
) -> dict[str, Any]:
    """
    Build GATEWAY_SNAPSHOT payload from WebSocket client state.

    Extracted from EdgeFederationServer._send_initial_telemetry().

    Args:
        ws_client: Gateway WebSocket client
        source: Telemetry source identifying this Edge

    Returns:
        Payload dict with resources, models, and catalog data

    Raises:
        ValueError: If ws_client methods are missing or return invalid data
    """
    # Get resources first (needed for filtering)
    try:
        resources = ws_client.get_resources()
        if resources is None:
            logger.error(
                "❌ ws_client.get_resources() returned None - "
                "Gateway not connected or state not initialized"
            )
            raise ValueError(
                "ws_client.get_resources() returned None - "
                "Gateway not connected or state not initialized"
            )
    except AttributeError as e:
        logger.error(
            f"❌ ws_client.get_resources() not available: {e}. "
            f"Gateway WebSocket client API mismatch. This is a code bug."
        )
        raise ValueError(
            f"ws_client.get_resources() method not found: {e}. "
            f"Check Gateway WebSocket client API compatibility."
        ) from e
    except Exception as e:
        logger.error(
            f"❌ Failed to get resources from Gateway WebSocket client: {e}. "
            f"Failing fast to prevent invalid telemetry upstream."
        )
        raise ValueError(
            f"Failed to extract Gateway resources for initial telemetry: {e}"
        ) from e

    # Get ALL available models (unfiltered)
    all_models = (
        list(ws_client.get_models()) if hasattr(ws_client, "get_models") else []
    )

    # Extract catalog data (model resources and activated contexts)
    model_resources: dict[str, dict[str, int | str]] = {}
    activated_contexts: dict[str, dict[str, list[int]]] = {}
    activated_models: list[str] = []

    try:
        catalog = ws_client.get_catalog()

        if catalog:
            # Forward full model resource entries from Gateway catalog.
            # Includes capacity metadata (context_length, parallel_slots,
            # effective_context_per_slot) needed by Master for max_tokens
            # capping when token counting is skipped (e.g. pipeline mode).
            catalog_resources = catalog.get("model_resources", {})
            for model_id in all_models:
                model_entry = catalog_resources.get(model_id, {})
                vram_usage = model_entry.get("vram_usage")
                ram_usage = model_entry.get("ram_usage")

                if vram_usage is not None and ram_usage is not None:
                    model_resources[model_id] = dict(model_entry)

            # Extract activated contexts (for Master filtering)
            activated_contexts = catalog.get("activated_contexts", {})

            # Apply activation filtering to get public-facing models
            # Master needs both lists: full for routing, filtered for /v1/models
            if activated_contexts:
                from systems.federation.remote.telemetry.snapshot import (
                    apply_activation_filtering,
                )

                activated_models_set = apply_activation_filtering(
                    ws_client, set(all_models), resources
                )
                activated_models = list(activated_models_set)
            else:
                # No activation rules: all models are "activated"
                activated_models = all_models

    except Exception as e:
        logger.error(f"Error extracting catalog data for initial telemetry: {e}")
        # Fallback: if we can't extract activation data, treat all as activated
        activated_models = all_models

    # Build initial payload from ws_client's current state
    # Use public API methods - fail fast if they don't exist (code bug)
    try:
        loaded_models = list(ws_client.get_loaded_models())
    except AttributeError as e:
        logger.error(
            f"❌ ws_client.get_loaded_models() not available: {e}. "
            f"Gateway WebSocket client API mismatch. This is a code bug."
        )
        raise ValueError(
            f"ws_client.get_loaded_models() method not found: {e}. "
            f"Check Gateway WebSocket client API compatibility."
        ) from e

    try:
        busy_models = list(ws_client.get_busy_models())
    except AttributeError as e:
        logger.error(
            f"❌ ws_client.get_busy_models() not available: {e}. "
            f"Gateway WebSocket client API mismatch. This is a code bug."
        )
        raise ValueError(
            f"ws_client.get_busy_models() method not found: {e}. "
            f"Check Gateway WebSocket client API compatibility."
        ) from e

    payload = {
        "available_vram_mb": resources.available_vram_mb,
        "available_ram_mb": resources.available_ram_mb,
        "total_vram_mb": resources.total_vram_mb,
        "total_ram_mb": resources.total_ram_mb,
        "loaded_models": loaded_models,
        "busy_models": busy_models,
        "loading_models": [],  # Start empty, updated by lifecycle events
        "available_models": all_models,  # Full list for routing
        "activated_models": activated_models,  # Filtered list for /v1/models
        "activated_contexts": activated_contexts,  # Activation rules
        "model_resources": model_resources,  # Resource requirements
        "source": source.to_dict(),
    }

    return payload


def create_periodic_heartbeat_task(
    ws_client: GatewayWebSocketClient,
    stargate_id: str,
    gateway_id: str,
    node_id: str,
    broadcast_callback: Callable[[dict[str, Any]], Any],
) -> asyncio.Task[None]:
    """
    Create periodic heartbeat task to prevent telemetry staleness.

    Sends telemetry updates every 5 seconds to keep Master's state fresh.
    Master has telemetry_staleness_threshold=10s, so 5s heartbeat ensures
    telemetry never becomes stale during idle periods.

    Args:
        ws_client: Gateway WebSocket client (for connection status check)
        stargate_id: Stargate identifier
        gateway_id: Gateway identifier
        node_id: Canonical node identifier
        broadcast_callback: Async callback to broadcast messages to peers

    Returns:
        Asyncio task running the heartbeat loop
    """

    async def heartbeat_loop() -> None:
        """Send periodic heartbeat with current cached state."""
        interval = 5.0  # 5s interval (staleness threshold is 10s)
        while True:
            await asyncio.sleep(interval)
            try:
                # Check if Gateway is still connected
                if not getattr(ws_client, "is_connected", True):
                    logger.debug("Gateway disconnected, skipping heartbeat")
                    continue

                # Send lightweight heartbeat (no catalog updates, just keepalive)
                from systems.federation.common.protocol.message import (
                    create_telemetry_heartbeat,
                )

                source: dict[str, str] = {
                    "stargate_id": stargate_id,
                    "gateway_id": gateway_id,
                }
                if node_id:
                    source["node_id"] = node_id

                heartbeat_msg = create_telemetry_heartbeat(
                    gateway_id=gateway_id,
                    source=source,
                )

                # Send as generic telemetry (lightweight, no pipeline reload)
                await broadcast_callback(heartbeat_msg.to_dict())

            except asyncio.CancelledError:
                logger.debug("Periodic heartbeat task cancelled")
                break
            except Exception as e:
                logger.warning(f"Periodic heartbeat error: {e}")

    task = asyncio.create_task(
        heartbeat_loop(), name="federation-periodic-telemetry-heartbeat"
    )
    logger.info("🔄 Started periodic telemetry heartbeat (interval=5s)")
    return task


def create_resource_update_callback(
    ws_client: GatewayWebSocketClient,
    forward_callback: Callable[[str, dict[str, Any]], Any],
) -> Callable[[dict[str, Any]], Any]:
    """
    Create resource update callback for Gateway WebSocket client.

    Args:
        ws_client: Gateway WebSocket client
        forward_callback: Callback to forward telemetry (cache_and_forward_telemetry)

    Returns:
        Async callback for resource updates
    """

    async def on_resource_update(data: dict[str, Any]) -> None:
        """
        Forward RESOURCE_UPDATE to connected peers.

        Note: We only send loaded_models/busy_models (dynamic state).
        We do NOT send available_models/model_resources on every update
        (Master preserves them from initial telemetry).
        """
        # Minimal telemetry: just resource metrics and loaded/busy state
        enriched_data = data.copy()

        # Remove static data (should only be in initial telemetry)
        enriched_data.pop("available_models", None)
        enriched_data.pop("model_resources", None)

        # Add dynamic model state (loaded/busy changes frequently)
        # Use public API - fail fast if methods don't exist (code bug)
        try:
            loaded_models = list(ws_client.get_loaded_models())
            busy_models = list(ws_client.get_busy_models())
        except AttributeError as e:
            logger.error(
                f"❌ Gateway WebSocket client missing required method: {e}. "
                f"This is a code bug - API mismatch."
            )
            raise ValueError(
                f"Gateway WebSocket client missing required method: {e}. "
                f"Check API compatibility."
            ) from e

        enriched_data["loaded_models"] = loaded_models
        enriched_data["busy_models"] = busy_models

        await forward_callback(
            FederationMessageType.RESOURCE_UPDATE.value,
            enriched_data,
        )

    return on_resource_update


def create_model_lifecycle_callbacks(
    forward_callback: Callable[[str, dict[str, Any]], Any],
) -> dict[str, Callable]:
    """
    Create model lifecycle callbacks for Gateway WebSocket client.

    Args:
        forward_callback: Callback to forward telemetry (cache_and_forward_telemetry)

    Returns:
        Dict with callbacks: on_model_loaded, on_model_unloaded,
        on_model_busy, on_model_idle
    """

    async def on_model_loaded(model_id: str, data: dict[str, Any]) -> None:
        """Forward MODEL_LOADED to connected peers."""
        payload = {"model_id": model_id, **data}
        await forward_callback(
            FederationMessageType.MODEL_LOADED.value,
            payload,
        )

    async def on_model_unloaded(model_id: str) -> None:
        """Forward MODEL_UNLOADED to connected peers."""
        payload = {"model_id": model_id}
        await forward_callback(
            FederationMessageType.MODEL_UNLOADED.value,
            payload,
        )

    async def on_model_busy(model_id: str) -> None:
        """Forward MODEL_BUSY to connected peers."""
        payload = {"model_id": model_id}
        await forward_callback(
            FederationMessageType.MODEL_BUSY.value,
            payload,
        )

    async def on_model_idle(model_id: str, data: dict[str, Any]) -> None:
        """Forward MODEL_IDLE to connected peers."""
        payload = {"model_id": model_id, **data}
        await forward_callback(
            FederationMessageType.MODEL_IDLE.value,
            payload,
        )

    return {
        "on_model_loaded": on_model_loaded,
        "on_model_unloaded": on_model_unloaded,
        "on_model_busy": on_model_busy,
        "on_model_idle": on_model_idle,
    }
