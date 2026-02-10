"""
Queue-based routing for model loading.

Handles waiting in queue and retry logic when immediate routing fails.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException
from model_id import ModelId
from universal_logging import get_logger

from ....errors import ModelErrorBuilder
from ...types import AttemptImmediateRoute
from ..status import ModelLoadingStatus

if TYPE_CHECKING:
    from gateways import GatewayInstance, SingleGatewayManager
    from systems.routing.selection.types import Gateway

    from .loading import ModelLoadingOperations
    from .types import ConfigHelper, ResourceManagerProvider

logger = get_logger(__name__)


async def await_queue_with_retry(
    model_id: ModelId,
    mock_request: dict,
    gateway_manager: SingleGatewayManager,
    loading_ops: ModelLoadingOperations,
    get_resource_manager: ResourceManagerProvider,
    config: ConfigHelper,
    attempt_immediate_route: AttemptImmediateRoute,
    request: None = None,  # FastAPI Request for client disconnection detection
    *,
    sticky: bool = True,
) -> GatewayInstance | Gateway:
    """Enqueue request and wait for gateway with model loaded.

    Returns:
        GatewayInstance for local gateways, Gateway for federated gateways

    Queue waiting is unbounded - we wait as long as needed for a gateway
    to become available. Only explicit errors (model crash, client disconnect)
    terminate the wait. The actual model loading operation has its own timeout.
    """
    start_time = time.time()
    queue_future: asyncio.Task | None = None

    try:
        # Serialize for gateway_manager call
        queue_future = asyncio.create_task(
            gateway_manager.await_execution_completion(str(model_id))
        )

        result = await _wait_for_queue_or_immediate(
            model_id,
            mock_request,
            queue_future,
            attempt_immediate_route,
            config,
            sticky=sticky,
        )
        if result is not None:
            return result

        try:
            gateway = await queue_future
        except Exception as queue_error:
            logger.debug(f"Queue error: {queue_error}, final immediate check...")
            gateway_instance, federated_gateway = await attempt_immediate_route(
                model_id, mock_request, sticky=sticky
            )
            if gateway_instance:
                return gateway_instance
            if federated_gateway:
                return federated_gateway
            raise

        return await _retry_load_loop(
            model_id,
            mock_request,
            gateway,
            gateway_manager,
            loading_ops,
            get_resource_manager,
            config,
            attempt_immediate_route,
            start_time,
            request,
            sticky=sticky,
        )

    except asyncio.CancelledError:
        # Client disconnected - ensure queue_future is cancelled for cleanup
        if queue_future and not queue_future.done():
            queue_future.cancel()
            try:
                await queue_future
            except asyncio.CancelledError:
                pass
        logger.info(f"Request cancelled for {model_id} (client disconnect)")
        raise
    except HTTPException:
        raise
    except asyncio.QueueFull:
        raise HTTPException(
            status_code=429,
            detail={"error": {"message": "Request queue full", "type": "queue_full"}},
        )
    except TimeoutError:
        logger.warning(f"Queue timeout for {model_id}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": f"Timeout waiting for {model_id}",
                    "type": "timeout",
                }
            },
        )


async def _wait_for_queue_or_immediate(
    model_id: ModelId,
    mock_request: dict,
    queue_future: asyncio.Task,
    attempt_immediate_route: AttemptImmediateRoute,
    config: ConfigHelper,
    *,
    sticky: bool,
) -> GatewayInstance | Gateway | None:
    """Wait for queue while polling for immediate route."""
    while not queue_future.done():
        gateway_instance, federated_gateway = await attempt_immediate_route(
            model_id, mock_request, sticky=sticky
        )
        if gateway_instance:
            queue_future.cancel()
            try:
                await queue_future
            except asyncio.CancelledError:
                pass
            logger.info(
                f"Immediate route for {model_id} on {gateway_instance.config.name}"
            )
            return gateway_instance
        if federated_gateway:
            queue_future.cancel()
            try:
                await queue_future
            except asyncio.CancelledError:
                pass
            logger.info(
                f"Immediate federated route for {model_id} on {federated_gateway.name}"
            )
            return federated_gateway
        await asyncio.sleep(config.check_interval)
    return None


async def _retry_load_loop(
    model_id: ModelId,
    mock_request: dict,
    gateway: GatewayInstance,
    gateway_manager: SingleGatewayManager,
    loading_ops: ModelLoadingOperations,
    get_resource_manager: ResourceManagerProvider,
    config: ConfigHelper,
    attempt_immediate_route: AttemptImmediateRoute,
    start_time: float,
    request: None = None,  # FastAPI Request for client disconnection detection
    *,
    sticky: bool,
) -> GatewayInstance | Gateway:
    """
    Retry loading model with re-routing on resource failures.

    Returns:
        GatewayInstance for local gateways, Gateway for federated gateways

    Queue waiting is unbounded - we wait as long as needed for a gateway
    to become available. Only explicit errors terminate the wait:
    - Model crash/error on gateway
    - Client disconnect

    The actual model loading operation (ensure_on_gateway) has its own timeout.

    Flow:
    1. Try ensure_on_gateway on current gateway
    2. If LOADED → return gateway
    3. If FAILED → attempt_immediate_route() for re-routing + eviction
    4. If re-route succeeds → continue with new gateway
    5. If re-route fails → wait for model.execution.completed event, then retry
    """
    attempt_count = 0  # For logging only, not used for timeout decisions
    current_gateway = gateway

    while True:
        elapsed = time.time() - start_time

        # Check client disconnect if request provided
        if request:
            try:
                if await request.is_disconnected():
                    logger.warning(f"Client disconnected waiting for {model_id}")
                    raise HTTPException(
                        status_code=499,
                        detail={
                            "error": {
                                "message": "Client disconnected",
                                "type": "client_disconnect",
                            }
                        },
                    )
            except AttributeError:
                pass

        # No queue timeout - wait indefinitely for gateway availability
        # Only explicit errors (model crash, gateway down, client disconnect) terminate
        # Periodic status logging for visibility
        if elapsed > 0 and int(elapsed) % 60 == 0:  # Every 60s
            logger.info(f"⏳ {model_id} waiting for gateway ({elapsed:.0f}s elapsed)")

        # Check for error state on current gateway
        gw_status = await loading_ops.get_model_status(current_gateway, model_id)
        if gw_status.is_error:
            error = gw_status.error_message or "Unknown"
            logger.error(f"{model_id} ERROR on {current_gateway.config.name}: {error}")
            raise ModelErrorBuilder.model_loading_failed(
                str(model_id),
                reason=f"Crashed on {current_gateway.config.name}: {error}",
            )

        resource_manager = get_resource_manager(current_gateway.config.name)
        if not resource_manager:
            logger.warning(
                f"No resource manager for {current_gateway.config.name}, re-routing"
            )
            # Try re-routing instead of just retrying
            gateway_instance, federated_gateway = await attempt_immediate_route(
                model_id, mock_request, sticky=sticky
            )
            if gateway_instance:
                current_gateway = gateway_instance
                continue
            if federated_gateway:
                return federated_gateway  # Federated takes over
            attempt_count += 1
            await asyncio.sleep(config.resource_retry_interval)
            continue

        status = await loading_ops.ensure_on_gateway(
            current_gateway, model_id, resource_manager, sticky=sticky
        )

        if status == ModelLoadingStatus.LOADED:
            logger.info(
                f"{model_id} loaded on {current_gateway.config.name} ({elapsed:.1f}s)"
            )
            return current_gateway

        elif status == ModelLoadingStatus.TIMED_OUT:
            raise ModelErrorBuilder.model_loading_timeout(
                str(model_id), config.model_loading_timeout
            )

        else:
            # FAILED status - this is where the bug was!
            # Before: just retry same gateway (no eviction)
            # After: re-route to trigger eviction on any gateway

            logger.debug(
                f"{model_id} failed on {current_gateway.config.name}, "
                f"attempting re-route with eviction..."
            )

            # Try full router re-invocation (includes Priority 3 eviction)
            gateway_instance, federated_gateway = await attempt_immediate_route(
                model_id, mock_request, sticky=sticky
            )

            if gateway_instance:
                if gateway_instance.config.name != current_gateway.config.name:
                    logger.info(
                        f"🔄 Re-routed {model_id} from "
                        f"{current_gateway.config.name} to "
                        f"{gateway_instance.config.name} (eviction may have occurred)"
                    )
                current_gateway = gateway_instance
                # Don't increment retry count - we got a new gateway
                continue

            if federated_gateway:
                logger.info(f"Re-routed to federated {federated_gateway.name}")
                return federated_gateway

            # Re-route failed, wait for a model availability signal.
            # Wait indefinitely - only wakes on model.execution.completed event
            # or client disconnect.
            if attempt_count == 0:
                logger.info(
                    f"⏳ {model_id} waiting for gateway to become available "
                    "(event-driven)"
                )
            attempt_count += 1

            await gateway_manager.execution_completion_waiter.wait_for_execution_completion(  # noqa: E501
                timeout_s=None  # Indefinite wait, event-driven only
            )

            # Event received - immediately try routing
            logger.debug(f"Gateway available signal for {model_id}, attempting route")
            gateway_instance, federated_gateway = await attempt_immediate_route(
                model_id, mock_request, sticky=sticky
            )
            if gateway_instance:
                current_gateway = gateway_instance
                continue
            if federated_gateway:
                return federated_gateway
            logger.debug(f"Route attempt failed for {model_id}, continuing wait")
