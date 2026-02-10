"""
Process lifecycle management for worker processes.

Handles process startup, shutdown, crash handling, and cleanup operations.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None
    logging.warning("psutil not available - process cleanup will be limited")

from process_ipc import (
    ProcessHealthConfig,
    ProcessSupervisor,
    SupervisorConfig,
    UnixSocketConfig,
)
from process_ipc import ResourceMonitoringConfig as ProcessIPCResourceConfig
from universal_logging import get_logger

from ..entrypoint import WorkerEntrypoint
from ..utils import (
    create_worker_environment,
    format_worker_command,
)
from .crash_callback import handle_process_crash_callback as _handle_crash_callback
from .kill import force_kill_process as _force_kill_process
from .state import ProcessState

logger = get_logger(__name__)
structured_logger = get_logger("universal_llm_gateway.workers.lifecycle")


class ProcessLifecycleManager:
    """
    Manages the lifecycle of worker processes.

    Handles process startup, shutdown, crash detection, and cleanup operations.
    """

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
        """
        Initialize the lifecycle manager.

        Args:
            state: Shared process state container
            worker_logs_dir: Directory for worker log files
            ipc_socket_dir: Directory for IPC socket files
            gateway_config: Gateway configuration object
            python_executable: Python executable path
            worker_entrypoint: Worker entrypoint specification (module or script)
            health_config: Process health monitoring configuration
            resource_config: Resource monitoring config (None = disabled)
            startup_timeout: Worker startup timeout
            shutdown_timeout: Worker shutdown timeout
        """
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
        transport_config_factory: Callable[[str], UnixSocketConfig],
        verify_process_alive_func: Callable[[str], Any],
        capture_diagnostics_func: Callable[[str, list, dict], Any],
    ) -> bool:
        """
        Start a new worker process for the specified model.

        Args:
            model_id: Model to start worker for
            transport_config_factory: Function to create transport config
            verify_process_alive_func: Function to verify process is alive
            capture_diagnostics_func: Function to capture diagnostics

        Returns:
            True if worker started successfully
        """
        logger.info(f"🚀 Starting worker for model: {model_id}")

        structured_logger.info(f"{model_id}:worker_starting: {model_id} - SUCCESS")

        try:
            # Check if supervisor already exists for this model
            if model_id in self.state.supervisors:
                logger.info(f"ℹ️ Supervisor already exists for {model_id}")
                # Check if worker is actually running
                if await verify_process_alive_func(model_id):
                    logger.info(f"✅ Process {model_id} is running and healthy")
                    return True
                else:
                    logger.warning(
                        "⚠️ Supervisor exists but process not alive, cleaning up..."
                    )
                    await self.cleanup_stale_process(model_id)

            # Build command for worker process
            from ..utils import get_universal_protocol_socket_path

            socket_file_path = get_universal_protocol_socket_path(model_id)
            log_file_path = f"{self.worker_logs_dir}/{model_id}.log"

            # Get idle timeout from gateway config (default 300s to match controller timeout)
            idle_timeout = getattr(self.gateway_config.streaming, "timeout", 300.0)

            command = format_worker_command(
                self.python_executable,
                self.worker_entrypoint,
                model_id,
                socket_file_path,
                log_file_path,
                idle_timeout=idle_timeout,
            )

            logger.info(f"Worker command: {' '.join(command)}")
            logger.info(f"Socket file path for {model_id}: {socket_file_path}")
            logger.info(f"Worker cwd: {self.worker_entrypoint.cwd}")

            # Ensure directories exist
            self.ipc_socket_dir.mkdir(parents=True, exist_ok=True)
            self.worker_logs_dir.mkdir(parents=True, exist_ok=True)

            # Set up environment variables
            env = create_worker_environment(model_id, self.python_executable)

            logger.info(f"Worker environment variables: {len(env)} variables")

            # Create transport config
            transport_config = transport_config_factory(socket_file_path)

            # Create consolidated SupervisorConfig
            # resource_config=None means monitoring disabled; preserve that semantics
            supervisor_config = SupervisorConfig(
                transport=transport_config,
                health=self.health_config,
                resource=self.resource_config or ProcessIPCResourceConfig(),
                worker_startup_timeout=60.0,  # Standard startup
                worker_shutdown_timeout=15.0,  # Standard shutdown
            )

            # Create ProcessSupervisor for this model (Supervised Actor Pattern)
            logger.info(
                f"📦 Creating ProcessSupervisor for {model_id} (v4.0.0 simplified)"
            )
            supervisor = ProcessSupervisor(config=supervisor_config)

            # Store supervisor and socket path in state
            self.state.set_supervisor(model_id, supervisor)
            self.state.set_socket_path(model_id, socket_file_path)

            # Spawn worker process
            logger.info(f"🚀 Spawning worker process for {model_id}...")
            success = await supervisor.spawn(
                worker_id=model_id,
                command=command,
                env=env,
                cwd=str(self.worker_entrypoint.cwd),
                startup_timeout=self.startup_timeout,
            )

            if success:
                logger.info(f"✅ Worker started successfully for {model_id}")

                # Update socket path to Universal Protocol path (per MVP convention)
                universal_protocol_socket_path = get_universal_protocol_socket_path(
                    model_id
                )
                self.state.set_socket_path(model_id, universal_protocol_socket_path)
                logger.info(
                    f"📍 Updated socket path for {model_id}: {universal_protocol_socket_path}"
                )

                # Clear from failed workers list if it was there
                self.state.failed_workers.discard(model_id)

                # Log successful worker startup
                structured_logger.info(
                    f"{model_id}:worker_started: {model_id} - SUCCESS"
                )
                return True
            else:
                logger.error(f"❌ Failed to start worker for {model_id}")

                # Log failed worker startup
                structured_logger.error(
                    f"{model_id}:worker_startup_failed: {model_id} - FAILED"
                )

                # Enhanced error capture
                await capture_diagnostics_func(model_id, command, env)
                return False

        except Exception as e:
            logger.error(f"❌ Error starting worker {model_id}: {e}")

            # Enhanced error capture
            try:
                await capture_diagnostics_func(model_id, command, env)
            except Exception as diag_error:
                logger.warning(
                    f"⚠️ Failed to capture diagnostic info for {model_id}: {diag_error}"
                )

            return False

    async def shutdown(self) -> bool:
        """
        Shutdown all workers.

        Returns:
            True if all workers shut down successfully
        """
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

            # Clear supervisor tracking
            self.state.supervisors.clear()
            self.state.socket_paths.clear()

            if success:
                logger.info("✅ All workers shut down successfully")
                return True
            else:
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
        """Crash callback (event-driven cleanup).
        
        Note: This is a background handler, but uses nowait for consistency.
        """
        await _handle_crash_callback(
            process_id, exit_code, error_message, self.state
        )

    async def force_kill_process(self, pid: int, model_id: str) -> bool:
        """
        Force kill a process by PID with SIGKILL.

        Delegates to kill module for actual termination logic.

        Args:
            pid: Process ID to kill
            model_id: Model identifier

        Returns:
            True if process terminated successfully
        """
        return await _force_kill_process(pid, model_id, self.gateway_config)

    async def fallback_process_cleanup(self, model_id: str) -> bool:
        """
        Fallback cleanup for untracked processes using event-driven socket cleanup.
        
        Publishes SocketCleanupRequested event (non-blocking) for processes that
        may have been started outside normal tracking. Used when process exists
        but isn't in our supervisor tracking.
        
        Invariant: event_published ∧ non_blocking ∧ process_terminated
        
        Args:
            model_id: Model identifier for the untracked process
            
        Returns:
            True if cleanup successful, False otherwise
            
        Side Effects:
            - Terminates process if found running
            - Publishes SocketCleanupRequested event (fire-and-forget)
            - Updates state tracking if process was found
            
        Note: 
            This method does NOT wait for socket cleanup completion.
            Event handler executes asynchronously in background.
        """
        try:
            logger.info(f"🧹 Performing fallback cleanup for {model_id}")

            # Check if process is actually running
            try:
                import psutil

                for proc in psutil.process_iter(["pid", "cmdline"]):
                    try:
                        cmdline = proc.info["cmdline"]
                        if cmdline and len(cmdline) > 2:
                            if (
                                cmdline[1].endswith("worker.py")
                                and len(cmdline) > 2
                                and cmdline[2] == model_id
                            ):
                                logger.info(
                                    f"🧹 Found orphaned worker process for {model_id} (PID: {proc.info['pid']}), terminating"
                                )
                                proc.terminate()
                                try:
                                    # Use configurable timeouts for orphaned process cleanup
                                    sigterm_timeout = float(
                                        getattr(
                                            self.gateway_config.process_isolation,
                                            "sigterm_wait_timeout",
                                            5,
                                        )
                                    )
                                    sigkill_timeout = float(
                                        getattr(
                                            self.gateway_config.process_isolation,
                                            "sigkill_wait_timeout",
                                            3,
                                        )
                                    )
                                    proc.wait(timeout=sigterm_timeout)
                                    logger.info(
                                        f"✅ Successfully terminated orphaned process {model_id}"
                                    )
                                except psutil.TimeoutExpired:
                                    logger.warning(
                                        f"⚠️ Process {model_id} did not terminate gracefully, force killing"
                                    )
                                    proc.kill()
                                    proc.wait(timeout=sigkill_timeout)
                                break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except ImportError:
                logger.warning("psutil not available for process cleanup")
            except Exception as e:
                logger.warning(f"Error during process cleanup: {e}")

            # Publish socket cleanup event (fire-and-forget, non-blocking)
            import asyncio

            from ...events import get_event_bus
            from ..utils import get_universal_protocol_socket_path
            from .communication import SocketCleanupRequested

            socket_path = get_universal_protocol_socket_path(model_id)
            # Schedule task without blocking (fire-and-forget from sync context)
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    lambda: asyncio.create_task(
                        get_event_bus().publish_async_nowait(
                            SocketCleanupRequested(model_id=model_id, socket_path=socket_path)
                        )
                    )
                )
            except RuntimeError:
                # Not in async context, skip emission
                pass

            return True

        except Exception as e:
            logger.error(f"❌ Error in fallback cleanup: {e}")
            return False

    async def verify_process_alive(
        self,
        model_id: str,
        get_all_process_info_func: Callable[[], dict[str, Any]],
    ) -> bool:
        """
        Verify if a process is actually alive by checking its PID.

        Args:
            model_id: Model ID to check
            get_all_process_info_func: Function to get all process info

        Returns:
            True if process is alive, False otherwise
        """
        try:
            all_processes = get_all_process_info_func()
            if model_id not in all_processes:
                return False

            process_info = all_processes[model_id]
            pid = (
                getattr(process_info, "pid", None)
                if hasattr(process_info, "pid")
                else process_info.get("pid")
                if isinstance(process_info, dict)
                else None
            )

            if not pid:
                return False

            # Check if process exists using psutil
            if psutil:
                return psutil.pid_exists(pid)
            else:
                # Fallback: try to send a signal to check if process exists
                try:
                    os.kill(
                        pid, 0
                    )  # Signal 0 doesn't actually send a signal, just checks if process exists
                    return True
                except (OSError, ProcessLookupError):
                    return False

        except Exception as e:
            logger.debug(f"Error verifying process {model_id}: {e}")
            return False

    async def cleanup_stale_process(self, model_id: str) -> None:
        """
        Clean up a stale process using event-driven socket cleanup.
        
        Publishes SocketCleanupRequested event (non-blocking) then removes
        supervisor and socket path from state tracking. Socket cleanup happens
        asynchronously via registered event handler.
        
        Invariant: event_published ∧ non_blocking ∧ state_removed
        
        Args:
            model_id: Model identifier for the stale process
            
        Side Effects:
            - Publishes SocketCleanupRequested event (fire-and-forget)
            - Removes supervisor from self.state.supervisors
            - Removes socket path from self.state.socket_paths
            
        Note: 
            This method does NOT wait for socket cleanup completion.
            Event handler executes asynchronously in background.
        """
        from ...events import get_event_bus
        from ..utils import get_universal_protocol_socket_path
        from .communication import SocketCleanupRequested

        try:
            logger.info(f"🧹 Cleaning up stale process {model_id}")

            # Publish socket cleanup event FIRST (fire-and-forget, non-blocking)
            # Event handler may need to read state, so publish before removal
            import asyncio

            socket_path = get_universal_protocol_socket_path(model_id)
            # Schedule task without blocking (fire-and-forget from sync context)
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon(
                    lambda: asyncio.create_task(
                        get_event_bus().publish_async_nowait(
                            SocketCleanupRequested(model_id=model_id, socket_path=socket_path)
                        )
                    )
                )
            except RuntimeError:
                # Not in async context, skip emission
                pass

            # Remove from tracking AFTER event publication
            self.state.remove_supervisor(model_id)
            self.state.remove_socket_path(model_id)

            logger.info(f"✅ Published cleanup event for {model_id}")

        except Exception as e:
            logger.warning(f"⚠️ Error cleaning up stale process {model_id}: {e}")

    async def is_process_alive(self, model_id: str) -> bool:
        """
        Simple liveness check - is the process responsive?

        Args:
            model_id: Model ID to check

        Returns:
            True if process exists and is responsive, False otherwise
        """
        supervisor = self.state.get_supervisor(model_id)
        if not supervisor:
            return False

        try:
            # Simple ping to check if process is responsive
            ping_command = {"command_type": "ping", "timestamp": time.time()}
            response = await supervisor.execute_command(ping_command, timeout=3.0)

            # Extract response and check for pong
            result = response
            return result and result.get("status") == "pong"

        except Exception as e:
            logger.debug(f"Process liveness check failed for {model_id}: {e}")
            return False
