"""Error handlers for model load failures during worker communication orchestration."""

import traceback
from typing import Any

from process_ipc import ProcessSupervisor
from universal_logging import get_logger

from .....errors import (
    SyntaxErrorException,
    WorkerInitializationError,
)
from ..cleanup import (
    cleanup_failed_worker,
    cleanup_syntax_error_worker,
    determine_error_type_and_code,
)
from ..error_handling import log_connection_error

logger = get_logger(__name__)


async def handle_load_failure(
    model_id: str,
    supervisor: ProcessSupervisor,
    gateway_config: Any,
    socket_path: str,
    failed_workers: set[str],
    error_msg: str,
    config_to_send: dict[str, Any],
) -> None:
    """Handle model load failure with cleanup and error raising (event-driven)."""
    logger.error(f"❌ Failed to load model in worker {model_id}: {error_msg}")

    error_type, error_code = determine_error_type_and_code(error_msg)

    await cleanup_failed_worker(
        model_id,
        supervisor,
        gateway_config,
        socket_path,
        error_msg,
    )

    failed_workers.add(model_id)

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
    """Handle Python syntax errors during model loading (event-driven)."""
    logger.error(f"❌ Syntax error in worker {model_id}: {error}")

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
    """Handle general exceptions during model loading (event-driven)."""
    log_connection_error(error, model_id, "worker_initialization")

    await cleanup_failed_worker(
        model_id,
        supervisor,
        gateway_config,
        socket_path,
        str(error),
    )
    failed_workers.add(model_id)

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
