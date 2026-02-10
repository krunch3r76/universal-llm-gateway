"""
Model load execution helpers.

Contains reservation and HTTP load request logic.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import HTTPException
from universal_logging import get_logger

from ..types import ResourceRequirements
from .status import ModelLoadingStatus, get_model_status
from .waiting import LoadResult, ModelLoadWaiter, UnloadResult

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ...gateway_error_interceptor import GatewayErrorInterceptor
    from ...resource_manager import GatewayResourceManager, ResourceReservation

logger = get_logger(__name__)


async def reserve_resources(
    resource_manager: GatewayResourceManager,
    model_id: str,
    gateway_name: str,
    requirements: ResourceRequirements,
) -> ResourceReservation | None:
    """
    Reserve resources for model loading.

    Args:
        resource_manager: Gateway resource manager
        model_id: Model to reserve for
        gateway_name: Gateway name for logging
        requirements: Resource requirements (REQUIRED - no defaults)

    Returns reservation on success, None on insufficient resources.
    """
    try:
        reservation = await resource_manager.reserve_resources_for_model(
            model=model_id,
            vram_mb=requirements.vram_mb,
            ram_mb=requirements.ram_mb,
        )
        if reservation:
            logger.info(
                f"🔒 Reserved {requirements.vram_mb}MB VRAM, "
                f"{requirements.ram_mb}MB RAM for {model_id}"
            )
        else:
            logger.warning(
                f"Insufficient resources for {model_id} on {gateway_name} "
                f"(needs {requirements.vram_mb}MB VRAM, {requirements.ram_mb}MB RAM)"
            )
        return reservation
    except Exception as e:
        logger.error(f"Resource reservation error: {e}", exc_info=True)
        return None


async def execute_load_request(
    gateway: GatewayInstance,
    model_id: str,
    gateway_interceptor: GatewayErrorInterceptor,
    resource_manager: GatewayResourceManager | None,
    reservation: ResourceReservation | None,
    timeout: int,
    load_waiter: ModelLoadWaiter | None = None,
) -> ModelLoadingStatus:
    """
    Execute model load request and wait for completion (event-driven).

    Uses WebSocket events via ModelLoadWaiter for instant notification.
    No polling - fully event-driven with timeout fallback to status check.

    CRITICAL: Always releases reservation in finally block to prevent resource leaks.
    """
    gateway_name = gateway.config.name

    try:
        if resource_manager and reservation:
            await resource_manager.mark_reservation_active(reservation.id)

        logger.info(f"🔄 [MODEL_LOAD] Initiating load for {model_id} on {gateway_name}")

        client = gateway.client.get_http_client()

        # Add explicit timeout to prevent hanging on non-existent model files
        try:
            response = await asyncio.wait_for(
                gateway_interceptor.safe_gateway_call(
                    client=client,
                    method="POST",
                    url=f"/api/v1/models/{model_id}/load",
                    gateway_name=gateway_name,
                    operation="model_load",
                ),
                timeout=30.0,  # 30 second timeout for initial load request
            )
        except TimeoutError:
            logger.error(
                f"❌ [MODEL_LOAD] Load request timed out for {model_id} on "
                f"{gateway_name} - likely missing model file"
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {
                        "message": f"Model {model_id} not found on gateway "
                        f"{gateway_name} - load request timed out",
                        "type": "model_not_found",
                        "code": "model_file_missing",
                        "model": model_id,
                        "gateway": gateway_name,
                    }
                },
            )

        logger.info(
            f"✅ [MODEL_LOAD] Load request sent for {model_id} "
            f"(status={response.status_code}), waiting for completion..."
        )

        # Event-driven wait for load completion
        if not load_waiter:
            logger.error(f"No load waiter configured for {model_id} - cannot wait")
            return ModelLoadingStatus.FAILED

        result = await load_waiter.wait_for_load(gateway_name, model_id, timeout)

        match result:
            case LoadResult.LOADED:
                logger.info(
                    f"✅ [MODEL_LOAD] Model {model_id} READY on {gateway_name} "
                    f"(event-driven)"
                )
                return ModelLoadingStatus.LOADED
            case LoadResult.FAILED:
                logger.error(
                    f"❌ [MODEL_LOAD] Model {model_id} FAILED to load on {gateway_name}"
                )
                return ModelLoadingStatus.FAILED
            case LoadResult.TIMEOUT:
                # Final status check on timeout
                status = await get_model_status(gateway, model_id)
                if status.is_loaded:
                    return ModelLoadingStatus.LOADED

                # Model still not loaded after timeout
                logger.error(
                    f"❌ [MODEL_LOAD] Model {model_id} timed out after {timeout}s "
                    f"on {gateway_name}"
                )
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {
                            "message": f"Model {model_id} not found on gateway "
                            f"{gateway_name} - load timed out after {timeout}s",
                            "type": "model_not_found",
                            "code": "model_file_missing",
                            "model": model_id,
                            "gateway": gateway_name,
                        }
                    },
                )
            case _:
                return ModelLoadingStatus.FAILED

    except HTTPException as http_exc:
        if _is_transient_connection_error(http_exc):
            logger.warning(
                f"Gateway connection error for {model_id}, treating as transient"
            )
            return ModelLoadingStatus.FAILED
        raise
    finally:
        # CRITICAL: Always release reservation to prevent resource leaks
        # This ensures resources are freed regardless of load outcome
        # (success, failure, timeout, or exception)
        if resource_manager and reservation:
            try:
                await resource_manager.release_reservation(reservation.id)
                logger.debug(
                    f"🔓 Released reservation for {model_id} on {gateway_name}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to release reservation for {model_id}: {e}",
                    exc_info=True,
                )


def _is_transient_connection_error(exc: HTTPException) -> bool:
    """Check if HTTPException represents a transient connection error."""
    if exc.status_code != 503:
        return False
    if not isinstance(exc.detail, dict):
        return False
    error = exc.detail.get("error", {})
    return "connection" in error.get("message", "").lower()


async def wait_for_unload(
    gateway: GatewayInstance,
    model_id: str,
    max_wait: int = 30,
    load_waiter: ModelLoadWaiter | None = None,
) -> bool:
    """
    Wait for model to finish unloading using event-driven mechanism.

    Uses WebSocket MODEL_UNLOADED events instead of HTTP polling.
    Critical for accurate VRAM release timing before loading new models.

    Args:
        gateway: Gateway instance
        model_id: Model being unloaded
        max_wait: Maximum seconds to wait
        load_waiter: Event-driven waiter (required for event-driven path)

    Returns:
        True if unload confirmed, False on timeout
    """
    gateway_name = gateway.config.name

    if load_waiter:
        # Event-driven: wait for MODEL_UNLOADED WebSocket event
        result = await load_waiter.wait_for_unload(gateway_name, model_id, max_wait)

        match result:
            case UnloadResult.UNLOADED:
                logger.info(
                    f"✅ Model {model_id} unloaded on {gateway_name} (event-driven)"
                )
                return True
            case UnloadResult.TIMEOUT:
                # Final status check on timeout
                status = await get_model_status(gateway, model_id)
                if status.status.value in ("not_found", "unloaded", "not_loaded"):
                    logger.info(
                        f"✅ Model {model_id} confirmed unloaded on {gateway_name} "
                        f"(status check after timeout)"
                    )
                    return True
                logger.warning(
                    f"⏰ Timeout waiting for {model_id} unload on {gateway_name}"
                )
                return False
            case UnloadResult.GATEWAY_UNREACHABLE:
                logger.warning(
                    f"Gateway {gateway_name} became unreachable during unload wait"
                )
                return False
            case _:
                return False
    else:
        # No waiter available - log error
        logger.error(
            f"No load waiter configured for unload wait on {model_id} - "
            f"cannot wait for unload"
        )
        return False
