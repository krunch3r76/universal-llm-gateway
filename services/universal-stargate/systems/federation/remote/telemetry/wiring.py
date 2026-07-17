"""
Telemetry wiring helpers for Remote mode.

Wires Gateway WebSocket callbacks to RemoteTelemetrySender.

INVARIANT: All payload construction via telemetry/snapshot.py helpers
INVARIANT: Filtering matrix from Phase 1 preserved:
  - initial/reconnect/resource_update: apply_filtering=True
  - periodic: apply_filtering=False
"""

import asyncio
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateway_websocket import GatewayWebSocketClient

    from .sender import RemoteTelemetrySender

logger = get_logger(__name__)


def wire_telemetry_callbacks(
    ws_client: "GatewayWebSocketClient",
    gateway_url: str,
    telemetry_sender: "RemoteTelemetrySender",
) -> "asyncio.Task[None]":
    """
    Wire all telemetry callbacks and start periodic/initial sends.

    Args:
        ws_client: Gateway WebSocket client
        gateway_url: URL for telemetry payloads
        telemetry_sender: Sender to Master

    Returns:
        Periodic telemetry task (for cancellation during shutdown)
    """
    _wire_resource_update_callback(ws_client, gateway_url, telemetry_sender)
    _wire_model_callbacks(ws_client, telemetry_sender)

    # Send initial snapshot
    _send_initial_telemetry(ws_client, gateway_url, telemetry_sender)

    # Start periodic telemetry (returns task for cancellation)
    return _start_periodic_telemetry(ws_client, gateway_url, telemetry_sender)


def _wire_resource_update_callback(
    ws_client: "GatewayWebSocketClient",
    gateway_url: str,
    telemetry_sender: "RemoteTelemetrySender",
) -> None:
    """Wire RESOURCE_UPDATE callback."""
    # CRITICAL: Import from Phase 1's snapshot module
    from .snapshot import build_telemetry_payload

    async def on_resource_update(_data: dict[str, Any]) -> None:
        """Forward RESOURCE_UPDATE to Master."""
        # INVARIANT: Resource updates apply filtering
        payload = build_telemetry_payload(ws_client, apply_filtering=True)

        _ = asyncio.create_task(
            telemetry_sender.on_resource_update(payload),
            name="federation-telemetry-resource",
        )

    ws_client.on_resource_update(on_resource_update)
    logger.debug("Wired on_resource_update callback")


def _wire_model_callbacks(
    ws_client: "GatewayWebSocketClient",
    telemetry_sender: "RemoteTelemetrySender",
) -> None:
    """Wire lifecycle callbacks (LOADING_STARTED, LOADED, LOAD_FAILED, UNLOADED)."""

    async def on_model_loading_started(model_id: str) -> None:
        """Forward MODEL_LOADING_STARTED to Master.

        Required so master-side subscribers (RAG AdmissionGate, etc.) see the
        cold-load window. Without this forward, master receives
        only MODEL_LOADED and MODEL_UNLOADED — the loading-window signal is
        invisible to host-side coordination.
        """
        _ = asyncio.create_task(
            telemetry_sender.on_model_loading_started(model_id),
            name=f"federation-telemetry-loading-started-{model_id}",
        )

    async def on_model_loading_progress(
        model_id: str, phase: str, pct: int | float
    ) -> None:
        """Forward MODEL_LOADING_PROGRESS heartbeat to Master."""
        _ = asyncio.create_task(
            telemetry_sender.on_model_loading_progress(model_id, phase, pct),
            name=f"federation-telemetry-loading-progress-{model_id}",
        )

    async def on_model_loaded(model_id: str, _data: dict[str, Any]) -> None:
        """Forward MODEL_LOADED to Master."""
        payload = {"model_id": model_id}
        _ = asyncio.create_task(
            telemetry_sender.on_model_loaded(payload),
            name=f"federation-telemetry-loaded-{model_id}",
        )

    async def on_model_load_failed(
        model_id: str,
        error: str,
        worker_snapshot: dict[str, Any] | None = None,
        gateway_state_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Forward MODEL_LOAD_FAILED to Master.

        Snapshots are forwarded so master-side ModelLoadingFailed emission
        carries the same worker/gateway-state forensics the edge captured.
        """
        _ = asyncio.create_task(
            telemetry_sender.on_model_load_failed(
                model_id,
                error,
                worker_snapshot=worker_snapshot,
                gateway_state_snapshot=gateway_state_snapshot,
            ),
            name=f"federation-telemetry-load-failed-{model_id}",
        )

    async def on_model_unloaded(model_id: str) -> None:
        """Forward MODEL_UNLOADED to Master."""
        payload = {"model_id": model_id}
        _ = asyncio.create_task(
            telemetry_sender.on_model_unloaded(payload),
            name=f"federation-telemetry-unloaded-{model_id}",
        )

    ws_client.on_model_loading_started(on_model_loading_started)
    ws_client.on_model_loading_progress(on_model_loading_progress)
    ws_client.on_model_loaded(on_model_loaded)
    ws_client.on_model_load_failed(on_model_load_failed)
    ws_client.on_model_unloaded(on_model_unloaded)
    logger.debug("Wired model lifecycle callbacks")


def _send_initial_telemetry(
    ws_client: "GatewayWebSocketClient",
    gateway_url: str,
    telemetry_sender: "RemoteTelemetrySender",
) -> None:
    """Send initial telemetry snapshot to Master."""
    # CRITICAL: Use Phase 1's snapshot helpers
    from .snapshot import build_telemetry_payload, log_snapshot_sent

    # INVARIANT: Initial telemetry applies filtering
    payload = build_telemetry_payload(ws_client, apply_filtering=True)

    _ = asyncio.create_task(
        telemetry_sender.on_resource_update(payload),
        name="federation-telemetry-initial",
    )

    log_snapshot_sent(
        "Sent initial telemetry to Master",
        len(payload["available_models"]),
        len(ws_client.get_models()),
        len(payload["loaded_models"]),
        len(payload["busy_models"]),
    )


def _start_periodic_telemetry(
    ws_client: "GatewayWebSocketClient",
    gateway_url: str,
    telemetry_sender: "RemoteTelemetrySender",
) -> "asyncio.Task[None]":
    """
    Start periodic telemetry task.

    Returns:
        Task reference for cancellation during shutdown
    """
    # CRITICAL: Use Phase 1's snapshot helpers
    from .snapshot import build_telemetry_payload

    async def periodic_telemetry_loop() -> None:
        """Send telemetry periodically."""
        interval = 5.0  # 5s interval (staleness threshold is 10s)
        while True:
            await asyncio.sleep(interval)
            try:
                if not ws_client.is_connected:
                    continue

                # INVARIANT: Periodic telemetry does NOT apply filtering
                # This is intentional - periodic is a freshness ping only
                payload = build_telemetry_payload(ws_client, apply_filtering=False)
                await telemetry_sender.on_resource_update(payload)

                logger.debug(
                    f"📤 Periodic telemetry: "
                    f"{len(payload['available_models'])} available"
                )
            except asyncio.CancelledError:
                logger.debug("Periodic telemetry task cancelled")
                break
            except Exception as e:
                logger.warning(f"Periodic telemetry error: {e}")

    task = asyncio.create_task(
        periodic_telemetry_loop(), name="federation-periodic-telemetry"
    )
    logger.info("🔄 Started periodic telemetry (interval=5s)")
    return task
