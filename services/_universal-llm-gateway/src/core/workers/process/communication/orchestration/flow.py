"""Model loading flow orchestration: config build, RPC, and response validation."""

import traceback
from typing import Any

from process_ipc import ProcessSupervisor
from process_ipc.core.exceptions import ProcessError
from universal_logging import get_logger

from .....errors import WorkerInitializationError
from ....utils import get_universal_protocol_socket_path
from ..config_builder import build_model_config_for_worker, get_worker_timeout
from ..error_handling import (
    create_timeout_error,
    create_worker_error_from_process_error,
    log_process_error,
)
from ..health_checks import run_preflight_checks, validate_load_response
from ..rpc_client import send_init_config_command, send_load_model_command
from .debug import emit_gateway_load_handshake_debug

logger = get_logger(__name__)


async def execute_model_loading_flow(
    model_id: str,
    supervisor: ProcessSupervisor,
    model_registry: Any,
    gateway_config: Any,
    correlation_id: str | None = None,
) -> tuple[bool, int | None, int | None, str | None]:
    """Execute the main model loading flow."""
    logger.info(f"📦 Building config for {model_id}")
    config_to_send = build_model_config_for_worker(
        model_id,
        model_registry,
        gateway_config,
    )
    await emit_gateway_load_handshake_debug(
        "config_built",
        model_id,
        correlation_id,
    )

    logger.info(f"📤 Sending config to worker {model_id}: {config_to_send}")

    timeout = get_worker_timeout(gateway_config)

    socket_path = get_universal_protocol_socket_path(model_id)
    await emit_gateway_load_handshake_debug(
        "preflight_start",
        model_id,
        correlation_id,
        socket_path=socket_path,
    )
    await run_preflight_checks(supervisor, socket_path, model_id)
    await emit_gateway_load_handshake_debug(
        "preflight_done",
        model_id,
        correlation_id,
    )

    try:
        await emit_gateway_load_handshake_debug(
            "init_config_start",
            model_id,
            correlation_id,
        )
        config_result = await send_init_config_command(
            supervisor,
            config_to_send,
            model_id,
            correlation_id,
            timeout=30.0,
        )
        await emit_gateway_load_handshake_debug(
            "init_config_done",
            model_id,
            correlation_id,
            has_error="error" in config_result,
        )
    except ProcessError as e:
        await emit_gateway_load_handshake_debug(
            "init_config_process_error",
            model_id,
            correlation_id,
            error=str(e),
        )
        log_process_error(e, model_id, "init_config")
        raise create_worker_error_from_process_error(
            e,
            model_id,
            {"command_type": "init_config", "config": config_to_send},
            "config_send",
        )
    except TimeoutError:
        await emit_gateway_load_handshake_debug(
            "init_config_timeout",
            model_id,
            correlation_id,
            timeout_s=30.0,
        )
        raise create_timeout_error(
            model_id,
            {"command_type": "init_config", "config": config_to_send},
            30.0,
            "config_send",
        )

    if "error" in config_result:
        error_msg = config_result["error"]
        await emit_gateway_load_handshake_debug(
            "init_config_failed",
            model_id,
            correlation_id,
            error=error_msg,
        )
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

    try:
        await emit_gateway_load_handshake_debug(
            "load_model_start",
            model_id,
            correlation_id,
            timeout_s=timeout,
        )
        load_result = await send_load_model_command(
            supervisor,
            model_id,
            correlation_id,
            timeout=timeout,
        )
        await emit_gateway_load_handshake_debug(
            "load_model_done",
            model_id,
            correlation_id,
            has_error=isinstance(load_result, dict) and "error" in load_result,
        )
    except ProcessError as e:
        await emit_gateway_load_handshake_debug(
            "load_model_process_error",
            model_id,
            correlation_id,
            error=str(e),
        )
        log_process_error(e, model_id, "load_model")
        raise create_worker_error_from_process_error(
            e,
            model_id,
            {"command_type": "load_model"},
            "model_load",
        )
    except TimeoutError:
        await emit_gateway_load_handshake_debug(
            "load_model_timeout",
            model_id,
            correlation_id,
            timeout_s=timeout,
        )
        raise create_timeout_error(
            model_id,
            {"command_type": "load_model"},
            timeout,
            "model_load",
        )
    except Exception as e:
        await emit_gateway_load_handshake_debug(
            "load_model_unexpected_error",
            model_id,
            correlation_id,
            error_type=type(e).__name__,
            error=str(e),
        )
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

    success, context_size, engine_pid, error_msg = validate_load_response(
        load_result, model_id
    )
    await emit_gateway_load_handshake_debug(
        "load_model_validated",
        model_id,
        correlation_id,
        success=success,
        context_size=context_size,
        engine_pid=engine_pid,
        error=error_msg,
    )

    return success, context_size, engine_pid, error_msg
