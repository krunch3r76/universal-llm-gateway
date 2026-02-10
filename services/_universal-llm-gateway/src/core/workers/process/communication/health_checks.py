"""Worker health validation and preflight checks."""

from typing import Any

from process_ipc import ProcessSupervisor
from universal_logging import get_logger

from ....errors import WorkerInitializationError
from .rpc_client import check_rpc_health as _check_rpc_health

logger = get_logger(__name__)


async def run_preflight_checks(
    supervisor: ProcessSupervisor,
    socket_path: str,
    model_id: str,
) -> None:
    """
    Run preflight checks before sending model config.

    Checks:
    1. Worker status is available
    2. RPC health check (optional - warning only)

    Args:
        supervisor: ProcessSupervisor instance
        socket_path: Universal Protocol socket path
        model_id: Model identifier

    Raises:
        WorkerInitializationError: If worker status unavailable
    """
    try:
        # Check worker status
        worker_status = supervisor.get_worker_status()
        if worker_status is None:
            raise WorkerInitializationError(
                message=f"Worker {model_id} status is unavailable",
                internal_error="get_worker_status() returned None",
                context={
                    "operation": "model_config_send",
                    "model_id": model_id,
                    "component": "worker_manager",
                },
            )

        logger.debug(f"Worker {model_id} status: {worker_status}")

        # Use RPC health check instead of legacy transport checks
        is_healthy = await _check_rpc_health(socket_path, model_id, timeout=5.0)
        if not is_healthy:
            logger.warning(
                f"⚠️ RPC health check failed for {model_id} during preflight - "
                + "worker may not be ready yet"
            )
            # Don't fail here - worker may still be starting up
        else:
            logger.debug(f"✅ RPC health check passed for {model_id}")

    except WorkerInitializationError:
        # Re-raise initialization errors as-is
        raise
    except Exception as preflight_error:
        # Log but continue - pre-flight checks are warnings, not blockers
        logger.warning(f"⚠️ Pre-flight check warning for {model_id}: {preflight_error}")


def validate_load_response(
    result: Any,
    model_id: str,
) -> tuple[bool, int | None, str | None]:
    """
    Validate load_model response from worker.

    Args:
        result: Response from worker
        model_id: Model identifier (for error messages)

    Returns:
        Tuple of (success, context_size, error_message)
        - success: True if model loaded
        - context_size: Context size if available
        - error_message: Error message if failed

    Note: Does not raise - returns validation result
    """
    # Check if result is not a dict
    if not isinstance(result, dict):
        error_msg = f"RPC response is not a dict: {type(result).__name__}"
        return False, None, error_msg

    # Check if result looks like metadata-only (protocol issue)
    metadata_fields = {"command_type", "worker_id"}
    if result.keys() <= metadata_fields:
        error_msg = (
            "Response contains only metadata (command_type, worker_id) "
            "but no worker response data"
        )
        return False, None, error_msg

    # Check for error in response
    if "error" in result:
        return False, None, result["error"]

    # Check status to verify model is loaded
    model_loaded = result.get("model_loaded", False)
    success = result.get("success", False)

    logger.debug(
        f"📤 DEBUG: Model loading check - model_loaded: {model_loaded}, "
        + f"success: {success}, full result: {result}"
    )

    if model_loaded or success:
        context_size = result.get("context_size")
        return True, context_size, None

    # Got a response but no success/model_loaded
    if result and not result.get("error"):
        error_msg = f"Worker returned unexpected response format: {result}"
    elif result.get("error"):
        error_msg = result.get("error")
    else:
        error_msg = "Worker did not return model_loaded=True or success=True"

    return False, None, error_msg
