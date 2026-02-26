"""
Process supervisor for single worker process - HTTP RPC implementation.

Manages process lifecycle using HTTP JSON-RPC to Universal Protocol server.
Replaces universal_transport with direct HTTP communication.
"""

import asyncio
import os
import signal
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..core.config import (
    SupervisorConfig,
)
from ..core.exceptions import ProcessError, TimeoutError
from ..core.messages import generate_correlation_id
from ..core.simple_health_monitor import SimpleHealthMonitor
from ..core.supervisor_state import (
    SupervisorEvent,
    SupervisorStateMachine,
)
from ..core.types import (
    ProcessHealth,
    ProcessInfo,
    ProcessResourceUsage,
    ProcessStatus,
)
from universal_logging import get_logger
from ..services.simple_resource_monitor import SimpleResourceMonitor
from ..utils.helpers import cleanup_socket_path


class ProcessSupervisor:
    """
    Process supervisor using HTTP RPC to Universal Protocol server.

    Manages a single worker process using JSON-RPC over HTTP to the
    Universal Protocol ASGI server running on the worker's Unix socket.
    """

    def __init__(self, config: SupervisorConfig):
        """Initialize the process supervisor."""
        # Store configuration
        self.config = config
        self._transport_config = config.transport
        self.log_base_dir = "/tmp/process_ipc"  # Fixed log directory
        self.health_config = config.health
        self.resource_config = config.resource

        # State machine for lifecycle management
        self._state_machine = SupervisorStateMachine()

        self._logger = get_logger("process_ipc.process.supervisor")
        self._structured_logger = get_logger("process_ipc.process.supervisor")

        # Single worker state
        self._worker_id: str | None = None
        self._worker_pid: int | None = None
        self._worker_process_info: ProcessInfo | None = None
        self._subprocess: subprocess.Popen | None = None
        self._log_file: Any | None = None

        # HTTP client for RPC communication
        self._http_client: httpx.AsyncClient | None = None
        self._rpc_endpoint: str | None = None

        # Resource monitoring
        self._resource_monitor = SimpleResourceMonitor(self.resource_config)

        # Health monitoring (always enabled)
        self._health_monitor: SimpleHealthMonitor = SimpleHealthMonitor(
            self.health_config
        )

        # Custom preexec function (optional)
        self._custom_preexec_fn: Callable | None = None

        # Shutdown flag
        self._shutdown_event = asyncio.Event()

        # Track active correlations for guaranteed cleanup
        self._active_correlations: set = set()

        # Track process group for cleanup
        self._worker_pgid: int | None = None

        # Set up emergency cleanup handlers for supervisor termination
        self.setup_emergency_cleanup_handler()

        self._structured_logger.info(
            f"process: supervisor:initialized - SUCCESS (log_base_dir={self.log_base_dir}, transport=http_rpc)"
        )

    def _get_universal_protocol_socket_path(self, model_id: str) -> str:
        """Get Universal Protocol socket path for a model."""
        return f"/tmp/universal-protocol/worker-{model_id}.sock"

    def _create_http_client(self, socket_path: str) -> httpx.AsyncClient:
        """Create HTTP client for Unix socket communication."""
        # Use httpx with Unix socket transport
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",  # Required but ignored for Unix sockets
            timeout=httpx.Timeout(30.0),  # Default 30s timeout
        )

    async def _rpc_call(
        self, method: str, params: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Make RPC call to supervisor endpoint."""
        return await self._make_rpc_call("/supervisor/rpc", method, params, timeout)

    async def _inference_rpc_call(
        self, method: str, params: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Make RPC call to inference endpoint."""
        return await self._make_rpc_call("/rpc", method, params, timeout)

    async def _make_rpc_call(
        self, endpoint: str, method: str, params: dict[str, Any], timeout: float = 30.0
    ) -> dict[str, Any]:
        """Make RPC call to specified endpoint."""
        if not self._http_client:
            raise ProcessError("HTTP client not initialized", self._worker_id)

        request_id = generate_correlation_id("rpc")

        # Add worker_id to params for compatibility (only for supervisor calls)
        if endpoint == "/supervisor/rpc" and self._worker_id:
            params = params.copy()
            params["worker_id"] = self._worker_id

        rpc_request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }

        # self._logger.info(
        #    f"🔤 Making RPC call to {endpoint} - method: {method}, timeout: {timeout}s"
        # )
        # self._logger.debug(f"🔤 RPC request: {rpc_request}")

        try:
            response = await self._http_client.post(
                endpoint, json=rpc_request, timeout=timeout
            )

            # Always read JSON response first to get detailed error info
            try:
                result = response.json()
            except Exception as json_err:
                # If we can't parse JSON, check status and raise appropriate error
                # Check status without raising to avoid exception propagation issues
                if response.is_error:
                    raise ProcessError(
                        f"Invalid JSON response (HTTP {response.status_code}): {json_err}",
                        self._worker_id,
                    )
                else:
                    # Status is OK but JSON is invalid
                    raise ProcessError(
                        f"Invalid JSON response: {json_err}", self._worker_id
                    )

            # Check for RPC error in JSON-RPC format
            if "error" in result:
                error_data = result["error"].get("data", {})
                error_message = error_data.get(
                    "message", result["error"].get("message", "RPC error")
                )
                error_code = error_data.get("code", "UNKNOWN")
                raise ProcessError(
                    f"RPC error [{error_code}]: {error_message}", self._worker_id
                )

            # Validate response ID matches request ID (JSON-RPC 2.0 requirement)
            response_id = result.get("id")
            if response_id != request_id:
                raise ProcessError(
                    f"RPC response ID mismatch: expected {request_id}, got {response_id}. "
                    f"This indicates a response routing error - responses may be getting mixed up between concurrent requests.",
                    self._worker_id,
                )

            # Now check HTTP status (should be 200 if no error in JSON)
            response.raise_for_status()

            final_result = result.get("result", {})
            if isinstance(final_result, dict) or (
                isinstance(final_result, list) and len(final_result) > 0
            ):
                pass
                # self._logger.info(f"🔤 RPC response for {method}: {final_result}")
            return final_result

        except httpx.TimeoutException:
            raise TimeoutError(
                f"RPC call '{method}' timed out after {timeout}s", self._worker_id
            )
        except httpx.ConnectError:
            raise ProcessError(
                f"Cannot connect to worker {self._worker_id}", self._worker_id
            )
        except ProcessError:
            raise
        except Exception as e:
            raise ProcessError(f"RPC error: {e}", self._worker_id)

    def _create_preexec_fn(self) -> Callable:
        """Create the preexec_fn callback for subprocess spawning."""
        from ..utils.helpers import (
            setup_enhanced_orphan_prevention,
        )

        def combined_preexec():
            # Enhanced orphan prevention with multiple layers
            setup_enhanced_orphan_prevention()

            # Optional: Advanced cgroup isolation (requires permissions)
            # Uncomment if running with appropriate system permissions:
            # setup_process_isolation_with_cgroups()

            # Call custom preexec_fn if provided
            if self._custom_preexec_fn:
                self._custom_preexec_fn()

        return combined_preexec

    async def spawn(
        self,
        worker_id: str,
        command: list[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        startup_timeout: float = 300.0,
        detached: bool = False,
    ) -> bool:
        """Spawn a new worker process."""
        # Check state machine allows spawn
        if not self._state_machine.can_transition(SupervisorEvent.SPAWN_REQUESTED):
            current_state = self._state_machine.get_current_state()
            raise ProcessError(
                f"Cannot spawn worker in state {current_state.value}", worker_id
            )

        # Transition to STARTING state
        if not self._state_machine.transition(
            SupervisorEvent.SPAWN_REQUESTED, {"worker_id": worker_id}
        ):
            raise ProcessError("Failed to transition to STARTING state", worker_id)

        if self._worker_id is not None:
            raise ProcessError(
                f"Worker {self._worker_id} already running. Stop it first.", worker_id
            )

        self._structured_logger.info(
            f"process: {worker_id}:spawning - SUCCESS (command={command})"
        )

        try:
            # Use Universal Protocol socket path
            socket_path = self._get_universal_protocol_socket_path(worker_id)

            # Ensure socket directory exists
            socket_dir = os.path.dirname(socket_path)
            if socket_dir:
                os.makedirs(socket_dir, exist_ok=True)

            # Clean up existing socket
            cleanup_socket_path(socket_path)

            # Prepare log file
            log_dir = Path(self.log_base_dir) / worker_id
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file_path = log_dir / f"{worker_id}.log"

            self._log_file = open(log_file_path, "w")

            # Create preexec_fn
            preexec_fn = self._create_preexec_fn()

            # Modify command to use Universal Protocol socket
            # Replace process_ipc socket path with universal protocol path
            modified_command = []
            for i, arg in enumerate(command):
                if i > 0 and command[i - 1] == "--socket-path":
                    # Replace socket path
                    modified_command.append(socket_path)
                else:
                    modified_command.append(arg)

            # Start subprocess
            self._subprocess = subprocess.Popen(
                modified_command,
                env=env,
                cwd=cwd,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                preexec_fn=preexec_fn,
            )

            # Store worker state
            self._worker_id = worker_id
            self._worker_pid = self._subprocess.pid

            # Try to get process group ID for cleanup
            try:
                self._worker_pgid = os.getpgid(self._worker_pid)
            except (OSError, ProcessLookupError):
                self._worker_pgid = None

            # Transition to CONNECTING state
            self._state_machine.transition(
                SupervisorEvent.WORKER_SPAWNED,
                {"worker_id": worker_id, "pid": self._worker_pid},
            )

            # Create process info
            self._worker_process_info = ProcessInfo(
                process_id=worker_id,
                pid=self._worker_pid,
                status=ProcessStatus.RUNNING,
                health=ProcessHealth.UNKNOWN,
                command=modified_command,
                socket_path=socket_path,
                started_at=datetime.now(),
                last_health_check=None,
                env=env,
                cwd=cwd,
            )

            # Set worker in resource monitor
            if self.resource_config.enable_resource_monitoring:
                self._resource_monitor.set_worker(worker_id, self._worker_pid)

            # Wait for socket to be created
            await self._wait_for_socket(socket_path, startup_timeout)

            # Create HTTP client
            self._http_client = self._create_http_client(socket_path)
            self._rpc_endpoint = "http://localhost/supervisor/rpc"

            # Verify connection with health check
            try:
                await self.health_check(timeout=5.0)

                # Transition to RUNNING state
                self._state_machine.transition(
                    SupervisorEvent.TRANSPORT_CONNECTED, {"transport_connected": True}
                )

            except Exception as e:
                self._logger.error(f"Failed to verify connection: {e}")
                raise ProcessError(f"Worker not responding: {e}", worker_id)

            # Start health monitoring
            if self._health_monitor:
                await self._health_monitor.start_monitoring(self)
                self._health_monitor.add_process_to_monitoring(worker_id)

            self._structured_logger.info(
                f"process: {worker_id}:spawned - SUCCESS (pid={self._worker_pid})"
            )

            return True

        except Exception as e:
            self._logger.error(f"Failed to spawn worker {worker_id}: {e}")
            # Transition to ERROR state
            self._state_machine.transition(
                SupervisorEvent.ERROR_OCCURRED,
                {"error": str(e), "worker_id": worker_id},
            )
            await self._cleanup_worker()
            raise ProcessError(f"Failed to spawn worker: {e}", worker_id)

    async def _wait_for_socket(self, socket_path: str, timeout: float) -> bool:
        """
        Wait for socket file to be created.

        ⚠️ POLLING JUSTIFIED: Startup-only, brief duration (<5s typical).

        ALTERNATIVE CONSIDERED:
        - inotify via watchdog/pyinotify - adds dependency, complex error handling
        - SOCKET_READY event from worker - requires protocol change

        DECISION: Keep polling. Complexity not justified for startup-only operation.
        """
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            if os.path.exists(socket_path):
                # Give server a moment to start accepting connections
                await asyncio.sleep(0.1)
                return True
            await asyncio.sleep(0.1)
        raise TimeoutError(
            f"Socket {socket_path} not created within timeout", self._worker_id
        )

    async def execute_command(
        self,
        command: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Execute a command and await response via HTTP RPC."""
        if not self._worker_id:
            raise ProcessError("No worker available for command", None)

        command_type = command.get("command_type", "unknown")
        params = command.copy()

        self._logger.info(
            f"🔄 Executing command '{command_type}' for worker {self._worker_id} with timeout {timeout}s"
        )

        try:
            # Use RPC call for process_command
            result = await self._rpc_call("process_command", params, timeout)

            self._logger.info(
                f"✅ Command '{command_type}' completed successfully - result: {result}"
            )
            return result

        except Exception as e:
            self._logger.error(f"❌ Error executing command '{command_type}': {e}")
            raise

    async def health_check(self, timeout: float = 5.0) -> dict[str, Any]:
        """Perform a health check on the worker."""
        if not self._worker_id:
            raise ProcessError("No worker available for health check", None)

        try:
            result = await self._rpc_call("health_check", {}, timeout)
            return result
        except Exception as e:
            raise ProcessError(f"Health check failed: {e}", self._worker_id)

    async def stop(
        self, worker_id: str = None, force: bool = False, timeout: float = 30.0
    ) -> bool:
        """Stop the worker process."""
        if worker_id and self._worker_id != worker_id:
            self._logger.warning(f"Worker {worker_id} not found or not running")
            return True

        if not self._worker_id:
            return True

        process_id = self._worker_id

        self._structured_logger.info(
            f"process: {process_id}:stopping - SUCCESS (force={force})"
        )

        try:
            # Stop health monitoring
            if self._health_monitor:
                await self._health_monitor.stop_monitoring()

            # Close HTTP client
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None

            # Stop subprocess and all its children
            if self._subprocess:
                try:
                    if self._subprocess.pid:
                        if force:
                            # Use enhanced force cleanup
                            await self.force_cleanup_process_tree(timeout)
                        else:
                            # Graceful termination first
                            try:
                                if self._worker_pgid:
                                    os.killpg(self._worker_pgid, signal.SIGTERM)
                                    self._logger.info(
                                        f"Terminated process group {self._worker_pgid}"
                                    )
                                else:
                                    self._subprocess.terminate()
                                    self._logger.info(
                                        f"Terminated process {self._subprocess.pid}"
                                    )
                            except (OSError, ProcessLookupError):
                                self._subprocess.terminate()

                            # Wait for graceful shutdown
                            try:
                                self._subprocess.wait(
                                    timeout=timeout / 2
                                )  # Use half timeout for graceful
                            except subprocess.TimeoutExpired:
                                # Force cleanup if graceful failed
                                self._logger.warning(
                                    "Graceful shutdown timed out, forcing cleanup"
                                )
                                await self.force_cleanup_process_tree(timeout / 2)

                except Exception as e:
                    self._logger.warning(f"Error stopping process: {e}")
                    # Final fallback - force cleanup
                    try:
                        await self.force_cleanup_process_tree(5.0)
                    except Exception as cleanup_error:
                        self._logger.error(f"Force cleanup failed: {cleanup_error}")

            # Cleanup
            await self._cleanup_worker()

            self._structured_logger.info(f"process: {process_id}:stopped - SUCCESS")

            return True

        except Exception as e:
            self._logger.error(f"Error stopping worker {process_id}: {e}")
            return False

    async def _cleanup_worker(self):
        """Clean up worker resources."""
        # Clear active correlations
        self._active_correlations.clear()

        # Close log file
        if self._log_file:
            try:
                self._log_file.close()
            except:
                pass
            self._log_file = None

        # Clean up socket
        if self._worker_id:
            socket_path = self._get_universal_protocol_socket_path(self._worker_id)
            cleanup_socket_path(socket_path)

        # Clear resource monitor
        self._resource_monitor.clear_worker()

        # Reset state
        self._worker_id = None
        self._worker_pid = None
        self._worker_process_info = None
        self._subprocess = None
        self._http_client = None
        self._rpc_endpoint = None

    def get_worker_info(self, worker_id: str = None) -> ProcessInfo | None:
        """Get information about the worker."""
        if worker_id and self._worker_id != worker_id:
            return None
        return self._worker_process_info

    def get_worker_status(self, worker_id: str = None) -> ProcessStatus | None:
        """Get the status of the worker."""
        if worker_id and self._worker_id != worker_id:
            return None
        if self._worker_process_info:
            return self._worker_process_info.status
        return None

    def is_worker_running(self, worker_id: str = None) -> bool:
        """Check if the worker is running."""
        if worker_id and self._worker_id != worker_id:
            return False
        if not self._subprocess:
            return False
        return self._subprocess.poll() is None

    def get_worker_exit_code(self, worker_id: str = None) -> int | None:
        """Get the exit code of the worker."""
        if worker_id and self._worker_id != worker_id:
            return None
        if self._subprocess:
            return self._subprocess.returncode
        return None

    async def send_command(self, command: dict[str, Any]) -> str:
        """Send a command and return correlation ID (for compatibility)."""
        correlation_id = generate_correlation_id("cmd")
        command = command.copy()
        command["correlation_id"] = correlation_id

        # For non-streaming commands, execute directly
        await self.execute_command(command)
        return correlation_id

    async def start_stream(self, command: dict[str, Any]) -> dict[str, Any]:
        """Start a streaming command and return stream info."""
        if not self._worker_id:
            raise ProcessError("No worker available for streaming", None)

        # Use inference RPC call to start_inference which handles streaming
        try:
            result = await self._inference_rpc_call(
                "start_inference", command, timeout=30.0
            )

            # The result should contain stream_id and websocket_path
            if not isinstance(result, dict) or "stream_id" not in result:
                raise ProcessError(
                    f"Invalid stream response: {result}", self._worker_id
                )

            return result

        except Exception as e:
            self._logger.error(f"Error starting stream: {e}")
            raise

    async def next_stream_event(
        self,
        correlation_id: str,
        timeout: float,
    ) -> dict[str, Any] | None:
        """Get next stream event (for compatibility)."""
        # This method is kept for compatibility but streaming now uses WebSocket directly
        # The actual streaming happens via WebSocket to /stream/{stream_id}
        raise NotImplementedError(
            "Streaming now uses WebSocket directly via /stream/{stream_id}"
        )

    def stop_stream(self, correlation_id: str) -> None:
        """Stop a stream (for compatibility)."""
        # This method is kept for compatibility but streaming now uses WebSocket directly
        # Stream cancellation happens via cancel_inference RPC call
        pass

    # Keep remaining methods unchanged (resource monitoring, process info, etc.)
    async def get_resource_usage(
        self, worker_id: str = None
    ) -> ProcessResourceUsage | None:
        """Get current resource usage for the worker."""
        if worker_id and self._worker_id != worker_id:
            return None
        return await self._resource_monitor.get_current_usage()

    def get_peak_usage(self, worker_id: str = None) -> dict[str, Any]:
        """Get peak resource usage for the worker."""
        if worker_id and self._worker_id != worker_id:
            return {
                "worker_id": None,
                "peak_ram_bytes": 0,
                "peak_ram_gb": 0,
                "peak_vram_bytes": 0,
                "peak_vram_gb": 0,
                "peak_timestamp": None,
            }
        return self._resource_monitor.get_peak_usage()

    def get_health_monitoring_status(self) -> dict[str, Any]:
        """Get health monitoring status."""
        return self._health_monitor.get_monitoring_status()

    def get_process_info(self) -> dict[str, Any]:
        """Get current process information."""
        return {
            "worker_id": self._worker_id,
            "pid": self._worker_pid,
            "status": self._worker_process_info.status.value
            if self._worker_process_info
            else None,
            "command": getattr(self._subprocess, "args", None)
            if self._subprocess
            else None,
            "started_at": self._worker_process_info.started_at.isoformat()
            if self._worker_process_info
            else None,
            "transport_connected": self._http_client is not None,
            "socket_path": self._get_universal_protocol_socket_path(self._worker_id)
            if self._worker_id
            else None,
            "health_monitoring": True,
            "resource_monitoring": self.resource_config.enable_resource_monitoring,
        }

    async def shutdown(self) -> None:
        """Shutdown the supervisor and stop all workers."""
        self._shutdown_event.set()

        if self._worker_id:
            await self.stop(force=True)

        # Stop health monitoring
        if self._health_monitor:
            await self._health_monitor.stop_monitoring()

        self._resource_monitor.shutdown()

        self._structured_logger.info("process: supervisor:shutdown - SUCCESS")

    async def force_cleanup_process_tree(self, timeout: float = 10.0) -> bool:
        """
        Force cleanup of entire process tree using multiple methods.

        Children are snapshotted before any kill so they remain trackable
        after the parent dies (reparenting to PID 1 would otherwise hide them).

        Args:
            timeout: Maximum time to wait for cleanup

        Returns:
            True if parent process confirmed dead within timeout, False otherwise
        """
        if not self._worker_pid:
            return True

        self._logger.info(f"Force cleanup of process tree for PID {self._worker_pid}")

        # Step 1: Snapshot children BEFORE any kills.
        # ∀ engines (vLLM EngineCore, llama-cpp-server) that spawn child subprocesses:
        # once the parent dies, children reparent to PID 1 and become untrackable.
        children: list[Any] = []
        try:
            import psutil
        except ImportError:
            psutil = None  # type: ignore[assignment]
        if psutil is not None:
            try:
                parent = psutil.Process(self._worker_pid)
                children = parent.children(recursive=True)
                if children:
                    child_pids = [c.pid for c in children]
                    self._logger.info(
                        f"Snapshotted {len(children)} child process(es) before kill "
                        f"(PIDs: {child_pids})"
                    )
            except psutil.NoSuchProcess:
                pass

        # Step 2: Kill process group (reaches children that stayed in group)
        if self._worker_pgid:
            try:
                os.killpg(self._worker_pgid, signal.SIGKILL)
                self._logger.info(f"Killed process group {self._worker_pgid}")
            except (OSError, ProcessLookupError) as e:
                self._logger.debug(f"Process group {self._worker_pgid} cleanup: {e}")

        # Step 3: Kill main process
        try:
            os.kill(self._worker_pid, signal.SIGKILL)
        except (OSError, ProcessLookupError) as e:
            self._logger.debug(f"Main process {self._worker_pid} cleanup: {e}")

        # Step 4: Wait for parent to die
        start_time = asyncio.get_event_loop().time()
        parent_dead = False
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                os.kill(self._worker_pid, 0)
                await asyncio.sleep(0.1)
            except (OSError, ProcessLookupError):
                parent_dead = True
                break

        if not parent_dead:
            self._logger.warning(
                f"Process {self._worker_pid} may still be alive after cleanup"
            )

        # Step 5: Kill children that survived (escaped process group or reparented)
        if psutil is not None:
            for child in children:
                try:
                    if child.is_running():
                        self._logger.warning(
                            f"Killing surviving child PID {child.pid} "
                            f"({child.name()}) for worker {self._worker_pid}"
                        )
                        child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            # Step 6: Wait for children
            if children:
                try:
                    psutil.wait_procs(children, timeout=min(timeout, 3.0))
                except Exception as e:
                    self._logger.debug(f"wait_procs during cleanup: {e}")

        # Step 7: Cgroup cleanup (unchanged)
        try:
            cgroup_path = Path(
                f"/sys/fs/cgroup/universal-llm-worker-{self._worker_pid}"
            )
            if cgroup_path.exists():
                procs_file = cgroup_path / "cgroup.procs"
                if procs_file.exists():
                    pids = procs_file.read_text().strip().split("\n")
                    for pid_str in pids:
                        if pid_str.strip():
                            try:
                                os.kill(int(pid_str), signal.SIGKILL)
                            except (ValueError, OSError, ProcessLookupError):
                                pass

                try:
                    cgroup_path.rmdir()
                    self._logger.debug(f"Removed cgroup {cgroup_path}")
                except OSError:
                    pass

        except Exception as e:
            self._logger.debug(f"Cgroup cleanup failed: {e}")

        return parent_dead

    def setup_emergency_cleanup_handler(self) -> None:
        """
        Set up emergency cleanup handler for the supervisor process.

        This ensures that if the supervisor itself is killed, it will
        attempt to clean up its worker processes.
        """

        def emergency_cleanup_handler(signum, frame):
            """Emergency cleanup when supervisor is killed."""
            if self._worker_pid:
                try:
                    # Try to kill process group first
                    if self._worker_pgid:
                        os.killpg(self._worker_pgid, signal.SIGKILL)
                    else:
                        os.kill(self._worker_pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass

        # Install handler for supervisor termination
        signal.signal(signal.SIGTERM, emergency_cleanup_handler)
        signal.signal(signal.SIGINT, emergency_cleanup_handler)

        # Use atexit as final fallback
        import atexit

        atexit.register(lambda: emergency_cleanup_handler(None, None))
