from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def wire_request_inference_started_event(proxy: StargateProxy) -> None:
    """Bridge Gateway runtime-start telemetry into Stargate request events."""
    if proxy.event_bus is None:
        logger.warning(
            "Cannot wire request inference start callback: event_bus not available"
        )
        return
    if proxy.gateway_manager is None:
        logger.warning(
            "Cannot wire request inference start callback: "
            "gateway_manager not available"
        )
        return

    gateway = proxy.gateway_manager.gateway
    if gateway is None:
        logger.warning(
            "Cannot wire request inference start callback: gateway not initialized"
        )
        return

    ws_client = gateway.client.ws_client

    from src.scheduling.events import RequestInferenceStarted

    async def _on_request_inference_started(
        request_id: str,
        model_id: str,
        gateway_url: str,
        correlation_id: str | None,
    ) -> None:
        try:
            await proxy.event_bus.publish_nowait(
                RequestInferenceStarted(
                    request_id=request_id,
                    model_id=model_id,
                    gateway_url=gateway_url,
                    correlation_id=correlation_id,
                )
            )
        except Exception:
            logger.exception(
                "Failed to publish request.inference.started from gateway telemetry: "
                "request_id=%s model_id=%s gateway_url=%s",
                request_id,
                model_id,
                gateway_url,
            )

    ws_client.on_request_inference_started(_on_request_inference_started)
    logger.info("Registered request.inference.started callback from Gateway telemetry")
