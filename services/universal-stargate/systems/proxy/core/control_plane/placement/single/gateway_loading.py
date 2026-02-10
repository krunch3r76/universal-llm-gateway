"""Gateway loading operations for model routing."""

from typing import TYPE_CHECKING

from fastapi import HTTPException
from model_id import ModelId
from universal_logging import get_logger

from ....errors import ModelErrorBuilder
from ...model_lifecycle.status import ModelLoadingStatus

if TYPE_CHECKING:
    from gateways import GatewayInstance

    from ...model_lifecycle.coordination import GlobalModelLoadCoordinator
    from ...model_lifecycle.loading import ModelLoadingOperations
    from ...types import ConfigHelper, ResourceManagerProvider

logger = get_logger(__name__)


def is_definitive_failure(error_msg: str) -> bool:
    """
    Check if error is a definitive failure that should not be retried.

    Definitive failures include:
    - OOM (Out of Memory) errors
    - Resource constraint errors
    - Model file not found

    These errors will not be resolved by retrying or queueing.
    """
    definitive_indicators = [
        "OOM:",
        "RESOURCE:",
        "out of memory",
        "oom",
        "cuda out of memory",
    ]
    return any(indicator in error_msg for indicator in definitive_indicators)


async def try_load_on_gateway(
    gateway: "GatewayInstance",
    model_id: ModelId,
    loading_ops: "ModelLoadingOperations",
    global_load_coordinator: "GlobalModelLoadCoordinator",
    get_resource_manager: "ResourceManagerProvider",
    config: "ConfigHelper",
    gateway_manager,  # Unused but kept for interface compatibility
    *,
    sticky: bool = True,
) -> "GatewayInstance | None":
    """
    Try loading model on gateway, raise HTTPException on failure.

    Returns:
        GatewayInstance if model loaded successfully.

    Raises:
        HTTPException: On any model loading failure (OOM, timeout, errors)
    """
    logger.debug(f"Attempting to load {model_id} on {gateway.config.name}")

    resource_manager = get_resource_manager(gateway.config.name)
    if not resource_manager:
        # BLOCKER: Resource manager should always exist for configured gateways.
        # If missing, it means registration failed during initialization.
        # Fail loudly rather than silently degrading to queue.
        gw_name = gateway.config.name
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": (
                        f"No resource manager for gateway '{gw_name}'. "
                        "Check startup logs for registration errors."
                    ),
                    "code": "resource_manager_missing",
                }
            },
        )

    try:
        status = await loading_ops.ensure_on_gateway(
            gateway, model_id, resource_manager, sticky=sticky
        )

        if status == ModelLoadingStatus.LOADED:
            # Single-gateway architecture: model is always on THE gateway
            logger.debug(f"✅ Model {model_id} ready on {gateway.config.name}")
            return gateway
        elif status == ModelLoadingStatus.TIMED_OUT:
            raise ModelErrorBuilder.model_loading_timeout(
                str(model_id), config.model_loading_timeout
            )
        else:
            # Check if model ended up in ERROR state with specific failure types
            status_result = await loading_ops.get_model_status(gateway, model_id)
            error_msg = None
            if status_result.is_error and status_result.error_message:
                error_msg = status_result.error_message

                if is_definitive_failure(error_msg):
                    logger.error(
                        f"Model {model_id} has definitive failure on "
                        + f"{gateway.config.name}: {error_msg}"
                    )
                    # Raise immediately with specific error type
                    if error_msg.startswith("OOM:"):
                        raise ModelErrorBuilder.model_oom_error(
                            str(model_id), error_msg
                        )
                    elif error_msg.startswith("RESOURCE:"):
                        raise ModelErrorBuilder.model_resource_error(
                            str(model_id), error_msg
                        )

            # All loading failures should raise immediately (no retries)
            error_detail = f": {error_msg}" if error_msg else ""
            logger.error(
                f"Model {model_id} failed to load on "
                f"{gateway.config.name}{error_detail}"
            )
            raise ModelErrorBuilder.model_loading_failed(
                str(model_id),
                reason=f"Load failed on {gateway.config.name}{error_detail}",
            )
    except HTTPException:
        # All HTTPExceptions should propagate immediately (no retries)
        # This includes OOM, resource constraints, timeouts, and other errors
        raise
