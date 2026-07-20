"""ProcessLifecycleManager coordinating worker startup, shutdown, and cleanup."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from process_ipc import ProcessHealthConfig
from process_ipc import ResourceMonitoringConfig as ProcessIPCResourceConfig
from universal_logging import get_logger

from ...entrypoint import WorkerEntrypoint
from ..crash_callback import handle_process_crash_callback as _handle_crash_callback
from ..state import ProcessState
from .cleanup import cleanup_stale_process, fallback_process_cleanup, kill_pid_tree
from .liveness import is_process_alive, verify_process_alive
from .start import start_worker

logger = get_logger(__name__)


class ProcessLifecycleManager:
    """Manages the lifecycle of worker processes."""

    def __init__(
        self,
        state: ProcessState,
        worker_logs_dir: Path,
        ipc_socket_dir: Path,
        gateway_config: Any,
        python_executable: str,
        worker_entrypoint: WorkerEntrypoint,
        health_config: ProcessHealthConfig,
        resource_config: ProcessIPCResourceConfig | None,
        startup_timeout: float,
        shutdown_timeout: float,
    ):
        self.state = state
        self.worker_logs_dir = worker_logs_dir
        self.ipc_socket_dir = ipc_socket_dir
        self.gateway_config = gateway_config
        self.python_executable = python_executable
        self.worker_entrypoint = worker_entrypoint
        self.health_config = health_config
        self.resource_config = resource_config
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout

    async def start_worker(
        self,
        model_id: str,
        transport_config_factory: Callable,
        verify_process_alive_func: Callable,
        capture_diagnostics_func: Callable,
    ) -> bool:
        return await start_worker(
            self,
            model_id,
            transport_config_factory,
            verify_process_alive_func,
            capture_diagnostics_func,
        )

    async def shutdown(self) -> bool:
        """Shutdown all workers."""
        logger.info("🛑 Shutting down all workers")

        try:
            success = True
            for model_id, supervisor in list(self.state.supervisors.items()):
                try:
                    logger.info(f"🛑 Stopping supervisor for {model_id}")
                    await supervisor.stop(timeout=10)
                    logger.info(f"✅ Supervisor for {model_id} stopped")
                except Exception as e:
                    logger.error(f"❌ Failed to stop supervisor for {model_id}: {e}")
                    success = False

            self.state.supervisors.clear()
            self.state.socket_paths.clear()

            if success:
                logger.info("✅ All workers shut down successfully")
                return True
            logger.error("❌ Failed to shut down some workers")
            return False

        except Exception as e:
            logger.error(f"❌ Error shutting down workers: {e}")
            return False

    async def handle_process_crash_callback(
        self,
        process_id: str,
        exit_code: int,
        error_message: str,
    ):
        await _handle_crash_callback(process_id, exit_code, error_message, self.state)

    @staticmethod
    async def kill_pid_tree(pid: int, model_id: str) -> bool:
        return await kill_pid_tree(pid, model_id)

    async def fallback_process_cleanup(self, model_id: str) -> bool:
        return await fallback_process_cleanup(self, model_id)

    async def verify_process_alive(
        self,
        model_id: str,
        get_all_process_info_func: Callable[[], dict[str, Any]],
    ) -> bool:
        return await verify_process_alive(model_id, get_all_process_info_func)

    async def cleanup_stale_process(self, model_id: str) -> None:
        await cleanup_stale_process(self.state, model_id)

    async def is_process_alive(self, model_id: str) -> bool:
        return await is_process_alive(self.state, model_id)
