"""Worker process startup: command assembly, supervisor spawn, and state tracking."""

from collections.abc import Callable
from typing import Any

from process_ipc import ProcessSupervisor, SupervisorConfig, UnixSocketConfig
from process_ipc import ResourceMonitoringConfig as ProcessIPCResourceConfig
from universal_logging import get_logger

from ...utils import (
    create_worker_environment,
    format_worker_command,
    get_universal_protocol_socket_path,
)
from ..state import ProcessState

logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.workers.lifecycle")


async def start_worker(
    manager: Any,
    model_id: str,
    transport_config_factory: Callable[[str], UnixSocketConfig],
    verify_process_alive_func: Callable[[str], Any],
    capture_diagnostics_func: Callable[[str, list, dict], Any],
) -> bool:
    """Start a new worker process for the specified model."""
    state: ProcessState = manager.state
    logger.info(f"🚀 Starting worker for model: {model_id}")
    structured_logger.info(f"{model_id}:worker_starting: {model_id} - SUCCESS")

    command: list[str] = []
    env: dict[str, str] = {}
    try:
        if model_id in state.supervisors:
            logger.info(f"ℹ️ Supervisor already exists for {model_id}")
            if await verify_process_alive_func(model_id):
                logger.info(f"✅ Process {model_id} is running and healthy")
                return True
            logger.warning("⚠️ Supervisor exists but process not alive, cleaning up...")
            await manager.cleanup_stale_process(model_id)

        socket_file_path = get_universal_protocol_socket_path(model_id)
        log_file_path = f"{manager.worker_logs_dir}/{model_id}.log"
        idle_timeout = getattr(manager.gateway_config.streaming, "timeout", 300.0)

        command = format_worker_command(
            manager.python_executable,
            manager.worker_entrypoint,
            model_id,
            socket_file_path,
            log_file_path,
            idle_timeout=idle_timeout,
        )

        logger.info(f"Worker command: {' '.join(command)}")
        logger.info(f"Socket file path for {model_id}: {socket_file_path}")
        logger.info(f"Worker cwd: {manager.worker_entrypoint.cwd}")

        manager.ipc_socket_dir.mkdir(parents=True, exist_ok=True)
        manager.worker_logs_dir.mkdir(parents=True, exist_ok=True)

        env = create_worker_environment(model_id, manager.python_executable)
        logger.info(f"Worker environment variables: {len(env)} variables")

        transport_config = transport_config_factory(socket_file_path)
        supervisor_config = SupervisorConfig(
            transport=transport_config,
            health=manager.health_config,
            resource=manager.resource_config or ProcessIPCResourceConfig(),
            worker_startup_timeout=60.0,
            worker_shutdown_timeout=15.0,
        )

        logger.info(f"📦 Creating ProcessSupervisor for {model_id} (v4.0.0 simplified)")
        supervisor = ProcessSupervisor(config=supervisor_config)

        state.set_supervisor(model_id, supervisor)
        state.set_socket_path(model_id, socket_file_path)

        logger.info(f"🚀 Spawning worker process for {model_id}...")
        success = await supervisor.spawn(
            worker_id=model_id,
            command=command,
            env=env,
            cwd=str(manager.worker_entrypoint.cwd),
            startup_timeout=manager.startup_timeout,
        )

        if success:
            logger.info(f"✅ Worker started successfully for {model_id}")
            universal_protocol_socket_path = get_universal_protocol_socket_path(
                model_id
            )
            state.set_socket_path(model_id, universal_protocol_socket_path)
            logger.info(
                f"📍 Updated socket path for {model_id}: {universal_protocol_socket_path}"
            )
            state.failed_workers.discard(model_id)
            structured_logger.info(f"{model_id}:worker_started: {model_id} - SUCCESS")
            return True

        logger.error(f"❌ Failed to start worker for {model_id}")
        structured_logger.error(
            f"{model_id}:worker_startup_failed: {model_id} - FAILED"
        )
        await capture_diagnostics_func(model_id, command, env)
        return False

    except Exception as e:
        logger.error(f"❌ Error starting worker {model_id}: {e}")
        try:
            await capture_diagnostics_func(model_id, command, env)
        except Exception as diag_error:
            logger.warning(
                f"⚠️ Failed to capture diagnostic info for {model_id}: {diag_error}"
            )
        return False
