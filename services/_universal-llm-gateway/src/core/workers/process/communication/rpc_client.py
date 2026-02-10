"""RPC client for worker communication."""

from typing import Any

from process_ipc import ProcessSupervisor
from universal_logging import get_logger
from universal_protocol.errors import RPCError
from universal_protocol.rpc.client import AsyncRPCClient

logger = get_logger(__name__)


async def check_rpc_health(
    socket_path: str,
    model_id: str,
    timeout: float = 5.0,
) -> bool:
    """
    Check worker health via Universal Protocol RPC health endpoint.

    This is the primary preflight check for worker readiness.

    Args:
        socket_path: Path to worker's Unix socket
        model_id: Model ID for logging context
        timeout: Health check timeout in seconds

    Returns:
        True if worker is responsive and ready for config, False otherwise
    """
    try:
        # Create RPC client to the worker socket
        client = AsyncRPCClient(socket_path, timeout=timeout, verify_socket=False)

        try:
            # Call health check RPC
            health_info = await client.health()
            logger.debug(f"✅ RPC health check passed for {model_id}: {health_info}")
            return True
        finally:
            # Clean up RPC client
            await client.close()

    except FileNotFoundError:
        logger.debug(
            f"Socket not ready for {model_id}: {socket_path} does not exist yet"
        )
        return False
    except (TimeoutError, RPCError) as e:
        logger.debug(f"RPC health check failed for {model_id}: {e}")
        return False
    except Exception as e:
        logger.debug(f"Unexpected error during RPC health check for {model_id}: {e}")
        return False


async def send_init_config_command(
    supervisor: ProcessSupervisor,
    config: dict[str, Any],
    model_id: str,
    correlation_id: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Send init_config command to worker.

    Args:
        supervisor: ProcessSupervisor instance
        config: Configuration dictionary
        model_id: Model identifier (for logging)
        correlation_id: Optional correlation ID
        timeout: Command timeout in seconds

    Returns:
        Command response dictionary

    Raises:
        Exception: On command failure
    """
    config_command = {"command_type": "init_config", "config": config}

    if correlation_id:
        config_command["correlation_id"] = correlation_id

    logger.info(f"📤 Step 1: Sending config to worker {model_id}")

    response = await supervisor.execute_command(config_command, timeout=timeout)

    logger.info(f"📤 DEBUG: Received config response: {response}")

    return response


async def send_load_model_command(
    supervisor: ProcessSupervisor,
    model_id: str,
    correlation_id: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """
    Send load_model command to worker.

    Args:
        supervisor: ProcessSupervisor instance
        model_id: Model identifier
        correlation_id: Optional correlation ID
        timeout: Load timeout in seconds

    Returns:
        Load response dictionary

    Raises:
        Exception: On command failure
    """
    load_command = {"command_type": "load_model"}

    if correlation_id:
        load_command["correlation_id"] = correlation_id

    logger.info(f"📤 Step 2: Executing load_model command for worker {model_id}")
    logger.debug(f"📤 DEBUG: Load command: {load_command}, timeout: {timeout}s")

    response = await supervisor.execute_command(load_command, timeout=timeout)

    logger.info(f"📤 Received load_model response (raw): {response}")
    logger.info(
        f"📤 Response type: {type(response)}, "
        + f"keys: {list(response.keys()) if isinstance(response, dict) else 'not a dict'}"
    )

    return response
