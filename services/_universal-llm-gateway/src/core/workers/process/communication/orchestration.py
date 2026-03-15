"""Model loading orchestration logic."""

import traceback
from typing import Any

from process_ipc import ProcessSupervisor
from process_ipc.core.exceptions import ProcessError
from universal_logging import get_logger

from ....errors import (
    SyntaxErrorException,
    WorkerInitializationError,
)
from ...utils import get_universal_protocol_socket_path
from .cleanup import (
    cleanup_failed_worker,
    cleanup_syntax_error_worker,
    determine_error_type_and_code,
)
from .config_builder import build_model_config_for_worker, get_worker_timeout
from .error_handling import (
    create_timeout_error,
    create_worker_error_from_process_error,
    log_connection_error,
    log_process_error,
)
from .health_checks import run_preflight_checks, validate_load_response
from .rpc_client import send_init_config_command, send_load_model_command

logger = get_logger(__name__)


async def execute_model_loading_flow(
    model_id: str,
    supervisor: ProcessSupervisor,
    model_registry: Any,
    gateway_config: Any,
    correlation_id: str | None = None,
) -> tuple[bool, int | None, int | None, str | None]:
    """
    Execute the main model loading flow.

    Steps:
    1. Build config from registry
    2. Run preflight checks
    3. Send init_config command
    4. Send load_model command
    5. Validate response

    Args:
        model_id: Model identifier
        supervisor: ProcessSupervisor instance
        model_registry: Model registry
        gateway_config: Gateway configuration
        correlation_id: Optional correlation ID

    Returns:
        Tuple of (success, context_size, engine_pid, error_msg)

    Raises:
        WorkerInitializationError: On validation or RPC failures
    """
    # Step 1: Build config from registry
    logger.info(f"📦 Building config for {model_id}")
    config_to_send = build_model_config_for_worker(
        model_id,
        model_registry,
        gateway_config,
    )

    logger.info(f"📤 Sending config to worker {model_id}: {config_to_send}")

    timeout = get_worker_timeout(gateway_config)

    # Step 2: Preflight checks
    socket_path = get_universal_protocol_socket_path(model_id)
    await run_preflight_checks(supervisor, socket_path, model_id)

    # Step 3: Send init_config command
    try:
        config_result = await send_init_config_command(
            supervisor,
            config_to_send,
            model_id,
            correlation_id,
            timeout=30.0,
        )
    except ProcessError as e:
        log_process_error(e, model_id, "init_config")
        raise create_worker_error_from_process_error(
            e,
            model_id,
            {"command_type": "init_config", "config": config_to_send},
            "config_send",
        )
    except TimeoutError:
        raise create_timeout_error(
            model_id,
            {"command_type": "init_config", "config": config_to_send},
            30.0,
            "config_send",
        )

    # Check config step result
    if "error" in config_result:
        error_msg = config_result["error"]
        logger.error(
            f"❌ Failed to initialize config in worker {model_id}: {error_msg}"
        )
        raise WorkerInitializationError(
            message=f"Config initialization failed for model {model_id}: {error_msg}",
            internal_error=f"Config command failed: {error_msg}",
            context={
                "operation": "config_initialization",
                "model_id": model_id,
                "component": "worker_manager",
                "config": config_to_send,
            },
        )

    logger.info(f"✅ Step 1: Config initialized successfully for {model_id}")

    # Step 4: Send load_model command
    try:
        load_result = await send_load_model_command(
            supervisor,
            model_id,
            correlation_id,
            timeout=timeout,
        )
    except ProcessError as e:
        log_process_error(e, model_id, "load_model")
        raise create_worker_error_from_process_error(
            e,
            model_id,
            {"command_type": "load_model"},
            "model_load",
        )
    except TimeoutError:
        raise create_timeout_error(
            model_id,
            {"command_type": "load_model"},
            timeout,
            "model_load",
        )
    except Exception as e:
        # Catch other exceptions (connection errors, etc.)
        logger.error(
            f"❌ Unexpected error loading model in worker {model_id}: {e}",
            exc_info=True,
        )
        raise WorkerInitializationError(
            message=f"Failed to load model in worker {model_id}",
            internal_error=f"Unexpected error: {str(e)}",
            stack_trace=traceback.format_exc(),
            context={
                "operation": "model_load",
                "model_id": model_id,
                "component": "worker_manager",
                "command": {"command_type": "load_model"},
                "exception_type": type(e).__name__,
            },
        )

    # Step 5: Validate load response
    success, context_size, engine_pid, error_msg = validate_load_response(
        load_result, model_id
    )

    return success, context_size, engine_pid, error_msg


async def handle_load_failure(
    model_id: str,
    supervisor: ProcessSupervisor,
    gateway_config: Any,
    socket_path: str,
    failed_workers: set[str],
    error_msg: str,
    config_to_send: dict[str, Any],
) -> None:
    """
    Handle model load failure with cleanup and error raising (event-driven).

    Args:
        model_id: Model identifier
        supervisor: ProcessSupervisor instance
        gateway_config: Gateway configuration
        socket_path: Path to worker socket
        failed_workers: Set of failed worker IDs
        error_msg: Error message
        config_to_send: Configuration that was sent

    Raises:
        SyntaxErrorException: If error is a syntax error
        WorkerInitializationError: For other initialization failures
    """
    logger.error(f"❌ Failed to load model in worker {model_id}: {error_msg}")

    # Determine error type
    error_type, error_code = determine_error_type_and_code(error_msg)

    # Cleanup failed process (event-driven)
    await cleanup_failed_worker(
        model_id,
        supervisor,
        gateway_config,
        socket_path,
        error_msg,
    )

    # Mark worker as failed
    failed_workers.add(model_id)

    # Raise appropriate error
    if error_type == "SYNTAX_ERROR":
        raise SyntaxErrorException(
            message=f"Syntax error in worker {model_id}",
            internal_error=error_msg,
            stack_trace=traceback.format_exc(),
            context={
                "operation": "worker_initialization",
                "model_id": model_id,
                "component": "worker_manager",
                "config": config_to_send,
            },
        )
    else:
        raise WorkerInitializationError(
            message=f"Failed to initialize worker {model_id}",
            error_type=error_type,
            error_code=error_code,
            internal_error=error_msg,
            stack_trace=traceback.format_exc(),
            context={
                "operation": "worker_initialization",
                "model_id": model_id,
                "component": "worker_manager",
                "config": config_to_send,
            },
        )


async def handle_syntax_error_exception(
    model_id: str,
    supervisor: ProcessSupervisor,
    gateway_config: Any,
    socket_path: str,
    failed_workers: set[str],
    error: SyntaxError,
) -> None:
    """
    Handle Python syntax errors during model loading (event-driven).

    Args:
        model_id: Model identifier
        supervisor: ProcessSupervisor instance
        gateway_config: Gateway configuration
        socket_path: Path to worker socket
        failed_workers: Set of failed worker IDs
        error: SyntaxError instance

    Raises:
        SyntaxErrorException: Always raises
    """
    logger.error(f"❌ Syntax error in worker {model_id}: {error}")

    # Clean up failed process (event-driven)
    await cleanup_syntax_error_worker(
        model_id,
        supervisor,
        gateway_config,
        socket_path,
    )
    failed_workers.add(model_id)

    raise SyntaxErrorException(
        message=f"Syntax error in worker {model_id}: {error}",
        internal_error=str(error),
        stack_trace=traceback.format_exc(),
        context={
            "operation": "worker_initialization",
            "model_id": model_id,
            "component": "worker_manager",
            "syntax_error_file": getattr(error, "filename", "unknown"),
            "syntax_error_line": getattr(error, "lineno", "unknown"),
        },
    )


async def handle_general_exception(
    model_id: str,
    supervisor: ProcessSupervisor,
    gateway_config: Any,
    socket_path: str,
    failed_workers: set[str],
    error: Exception,
) -> None:
    """
    Handle general exceptions during model loading (event-driven).

    Args:
        model_id: Model identifier
        supervisor: ProcessSupervisor instance
        gateway_config: Gateway configuration
        socket_path: Path to worker socket
        failed_workers: Set of failed worker IDs
        error: Exception instance

    Raises:
        WorkerInitializationError: Always raises
    """
    log_connection_error(error, model_id, "worker_initialization")

    # Clean up failed process (event-driven)
    await cleanup_failed_worker(
        model_id,
        supervisor,
        gateway_config,
        socket_path,
        str(error),
    )
    failed_workers.add(model_id)

    # Raise enhanced error
    raise WorkerInitializationError(
        message=f"Failed to initialize worker {model_id}: {str(error)}",
        internal_error=str(error),
        stack_trace=traceback.format_exc(),
        context={
            "operation": "worker_initialization",
            "model_id": model_id,
            "component": "worker_manager",
        },
    )
