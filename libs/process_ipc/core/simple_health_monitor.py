"""
Simplified health monitoring for ProcessSupervisor.

Provides basic health monitoring capabilities that work with the ProcessSupervisor
architecture without requiring complex state management.
"""

import asyncio
import signal
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from universal_event_bus import Event
from universal_logging import get_logger

from . import signals
from .config import ProcessHealthConfig
from .exceptions import ProcessHealthError


class SimpleHealthMonitor:
    """
    Simplified health monitor for ProcessSupervisor.

    Provides basic health monitoring without complex state management.
    """

    def __init__(self, config: ProcessHealthConfig):
        """
        Initialize the health monitor.

        Args:
            config: Health monitoring configuration
        """
        self.config = config
        self._monitoring = False
        self._monitor_task: asyncio.Task | None = None
        self._logger = get_logger("process_ipc.core.simple_health_monitor")
        self._monitored_processes: set[str] = set()
        self._last_health_checks: dict[str, datetime] = {}

        # Track PIDs that have already been reported as crashed (one-shot reporting)
        self._reported_crash_pids: set[int] = set()
        self._reported_exit_pids: set[int] = set()

        self._logger.info(
            f"SimpleHealthMonitor initialized with config: {config.__dict__}"
        )

        # Validate event bus configuration if provided
        if config.event_bus:
            self._validate_event_bus()

    def _validate_event_bus(self) -> None:
        """Validate that event bus is properly configured."""
        if not hasattr(self.config, "event_bus"):
            raise ValueError("event_bus not configured in ProcessHealthConfig")

        if not hasattr(self.config.event_bus, "publish"):
            raise ValueError(
                "event_bus must have publish method (universal_event_bus v0.2.0+)"
            )

    def _create_event(
        self,
        signal: str,
        payload: dict[str, Any],
        event_id: str = None,
        correlation_id: str = None,
    ) -> Event:
        """Create a properly formatted Event instance."""
        # Add metadata to payload since Event only accepts signal and payload
        enhanced_payload = payload.copy()
        enhanced_payload.update(
            {
                "id": event_id or f"{signal.lower()}_{int(time.time() * 1000)}",
                "timestamp": time.time(),
                "correlation_id": correlation_id,
            }
        )

        return Event(signal=signal, payload=enhanced_payload)

    async def start_monitoring(self, supervisor) -> None:
        """Start background health monitoring loop."""
        if self._monitoring:
            self._logger.warning("Health monitoring is already running")
            return

        # Health monitoring is always enabled

        self._monitoring = True
        self._supervisor = supervisor

        self._logger.info("Starting simple health monitoring")
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        self._logger.info(
            f"Health monitoring started with interval: {self.config.health_check_interval}s"
        )

    async def stop_monitoring(self) -> None:
        """Stop health monitoring."""
        if not self._monitoring:
            return

        self._monitoring = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        self._logger.info("Health monitoring stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop that checks processes periodically."""
        self._logger.info("Health monitoring loop started")

        try:
            while self._monitoring:
                try:
                    # Check if supervisor has a worker
                    if self._supervisor._worker_id and self._supervisor._subprocess:
                        await self._check_process_health(self._supervisor._worker_id)

                    # Wait for next check interval
                    await asyncio.sleep(self.config.health_check_interval)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self._logger.error(f"Error in health monitoring loop: {e}")
                    await asyncio.sleep(5.0)  # Brief pause on error

        except asyncio.CancelledError:
            self._logger.info("Health monitoring loop cancelled")
        except Exception as e:
            self._logger.error(f"Health monitoring loop failed: {e}")
        finally:
            self._logger.info("Health monitoring loop stopped")

    async def _check_process_health(self, process_id: str) -> bool:
        """
        Check if a specific process is healthy.

        Enhanced with crash detection and event publishing.

        Args:
            process_id: Process identifier

        Returns:
            bool: True if healthy, False if dead/unhealthy
        """
        # self._logger.debug(f"Checking health for process {process_id}")

        try:
            # Check if we should skip this process (rate limiting)
            if process_id in self._last_health_checks:
                last_check = self._last_health_checks[process_id]
                if datetime.now() - last_check < timedelta(
                    seconds=self.config.health_check_interval
                ):
                    return True

            self._last_health_checks[process_id] = datetime.now()

            # Get subprocess object
            subprocess_obj = self._supervisor._subprocess
            if not subprocess_obj:
                self._logger.warning(f"No subprocess object found for {process_id}")
                return False

            # Check if process is still running
            returncode = subprocess_obj.poll()
            pid = subprocess_obj.pid

            # Check if we've already reported this PID as crashed/exited
            if returncode is not None:
                if pid in self._reported_crash_pids or pid in self._reported_exit_pids:
                    # Already reported, skip further processing
                    # self._logger.debug(f"PID {pid} already reported, skipping")
                    return False

            if returncode is None:
                # Process is still running - healthy
                # self._logger.debug(f"Process {process_id} is healthy (PID: {subprocess_obj.pid})")
                return True
            else:
                # Process has terminated - handle crash detection
                self._logger.warning(
                    f"Process {process_id} terminated (PID: {pid}, "
                    f"exit code: {returncode})"
                )

                # Determine if this is a crash or clean exit
                is_crash = (
                    self.config.detect_crashes
                    and self.config.is_crash_exit_code(returncode)
                )

                if is_crash:
                    # Handle crash detection
                    await self._handle_process_crash(process_id, returncode, pid)
                    # Mark PID as reported to prevent duplicate crash events
                    self._reported_crash_pids.add(pid)
                else:
                    # Handle clean exit
                    await self._handle_process_exit(process_id, returncode)
                    # Mark PID as reported to prevent duplicate exit events
                    self._reported_exit_pids.add(pid)

                # Attempt automatic recovery if enabled
                if self.config.auto_recovery:
                    await self._attempt_recovery(process_id)

                return False

        except Exception as e:
            self._logger.error(f"Error checking health for process {process_id}: {e}")
            raise ProcessHealthError(
                f"Health check failed for {process_id}: {e}"
            ) from e

    async def _attempt_recovery(self, process_id: str) -> None:
        """
        Attempt to recover a dead process.

        Args:
            process_id: Process identifier
        """
        self._logger.info(f"Attempting recovery for process {process_id}")

        try:
            # Get original process info
            process_info = self._supervisor.get_process_info()
            if not process_info.get("command"):
                raise ProcessHealthError(
                    f"No process command available for recovery of {process_id}"
                )

            # Attempt to restart the process
            success = await self._supervisor.spawn(
                worker_id=process_id,
                command=process_info["command"],
                env=process_info.get("env"),
                cwd=process_info.get("cwd"),
                startup_timeout=self.config.recovery_timeout,
            )

            if not success:
                raise ProcessHealthError(f"Failed to recover process {process_id}")

            self._logger.info(f"Successfully recovered process {process_id}")

        except Exception as e:
            self._logger.error(f"Error during recovery attempt for {process_id}: {e}")
            raise ProcessHealthError(f"Recovery failed for {process_id}: {e}") from e

    def add_process_to_monitoring(self, process_id: str) -> None:
        """Add a process to health monitoring."""
        self._monitored_processes.add(process_id)
        self._logger.debug(f"Added process {process_id} to health monitoring")

    def remove_process_from_monitoring(self, process_id: str) -> None:
        """Remove a process from health monitoring."""
        self._monitored_processes.discard(process_id)
        self._last_health_checks.pop(process_id, None)
        self._logger.debug(f"Removed process {process_id} from health monitoring")

    def clear_reported_pid(self, pid: int) -> None:
        """Clear a PID from the reported crash/exit tracking (for new worker with same"
        "ID)."""
        self._reported_crash_pids.discard(pid)
        self._reported_exit_pids.discard(pid)
        self._logger.debug(f"Cleared reported status for PID {pid}")

    def get_monitoring_status(self) -> dict[str, Any]:
        """Get current monitoring status."""
        return {
            "monitoring": self._monitoring,
            "monitored_processes": list(self._monitored_processes),
            "config": self.config.__dict__,
            "last_health_checks": {
                pid: timestamp.isoformat()
                for pid, timestamp in self._last_health_checks.items()
            },
        }

    async def _handle_process_crash(
        self, process_id: str, exit_code: int, pid: int | None
    ) -> None:
        """
        Handle process crash detection and event publishing.

        Args:
            process_id: Process identifier
            exit_code: Process exit code
            pid: Process PID (may be None)
        """
        # Determine crash reason
        if exit_code < 0:
            # Signal-based termination
            signal_num = -exit_code
            signal_name = (
                signal.Signals(signal_num).name
                if signal_num < 32
                else f"SIG_{signal_num}"
            )
            error_message = f"Process killed by signal {signal_name} ({signal_num})"
        else:
            error_message = f"Process exited with error code {exit_code}"

        self._logger.error(f"🚨 Process crash detected: {process_id} - {error_message}")

        # Capture stderr if enabled
        stderr_output = None
        if self.config.capture_stderr_on_crash:
            stderr_output = await self._capture_process_stderr(process_id)

        # Call crash callback with timeout protection
        if self.config.on_process_crash:
            try:
                await asyncio.wait_for(
                    self._invoke_callback(
                        self.config.on_process_crash,
                        process_id,
                        exit_code,
                        error_message,
                    ),
                    timeout=self.config.crash_callback_timeout,
                )
            except TimeoutError:
                self._logger.error(
                    f"Crash callback timed out after {self.config.crash_callback_timeout}s"
                )
            except Exception as e:
                self._logger.error(f"Error in crash callback: {e}")

        # Publish crash event to event bus
        if self.config.event_bus and self.config.publish_crash_events:
            await self._publish_crash_event(
                process_id, exit_code, error_message, pid, stderr_output
            )

    async def _handle_process_exit(self, process_id: str, exit_code: int) -> None:
        """
        Handle clean process exit.

        Args:
            process_id: Process identifier
            exit_code: Process exit code
        """
        self._logger.info(f"Process {process_id} exited cleanly with code {exit_code}")

        # Call exit callback with timeout protection
        if self.config.on_process_exit:
            try:
                await asyncio.wait_for(
                    self._invoke_callback(
                        self.config.on_process_exit, process_id, exit_code
                    ),
                    timeout=self.config.crash_callback_timeout,
                )
            except TimeoutError:
                self._logger.error(
                    f"Exit callback timed out after {self.config.crash_callback_timeout}s"
                )
            except Exception as e:
                self._logger.error(f"Error in exit callback: {e}")

    async def _invoke_callback(self, callback: Callable, *args) -> None:
        """
        Safely invoke a callback function (sync or async).

        Args:
            callback: Callback function to invoke
            *args: Arguments to pass to callback
        """
        if asyncio.iscoroutinefunction(callback):
            await callback(*args)
        else:
            # Run sync callback in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, callback, *args)

    async def _publish_crash_event(
        self,
        process_id: str,
        exit_code: int,
        error_message: str,
        pid: int | None,
        stderr_output: str | None,
    ) -> None:
        """
        Publish crash event using proper Event instance.

        Args:
            process_id: Process identifier
            exit_code: Process exit code
            error_message: Human-readable error message
            pid: Process PID
            stderr_output: Captured stderr output (if available)
        """
        try:
            crash_event = signals.ProcessCrashDetected(
                process_id=process_id,
                error_message=error_message,
                exit_code=exit_code,
                pid=pid,
                socket_path=getattr(
                    self._supervisor._transport_config, "socket_path", None
                ),
                stderr=stderr_output,
                is_signal_termination=exit_code < 0,
                signal_name=(
                    signal.Signals(-exit_code).name
                    if exit_code < 0 and -exit_code < 32
                    else None
                ),
            )

            # Publish event in background
            loop = asyncio.get_event_loop()
            loop.create_task(self._publish_event_async(crash_event))

            self._logger.info(
                f"📢 Published crash event for {process_id} (exit code {exit_code})"
            )

        except Exception as e:
            self._logger.error(f"Failed to publish crash event: {e}")

    async def _publish_event_async(self, event: Event) -> None:
        """Publish Event instance directly using async API."""
        try:
            await self.config.event_bus.publish(event)
        except Exception as e:
            self._logger.error(f"Error publishing event asynchronously: {e}")

    async def _capture_process_stderr(self, process_id: str) -> str | None:
        """
        Capture stderr output from crashed process log file.

        Args:
            process_id: Process identifier

        Returns:
            Optional[str]: Stderr content or None if unavailable
        """
        try:
            # Get log file path from supervisor
            log_file = self._supervisor._log_file
            if log_file and hasattr(log_file, "name"):
                log_path = log_file.name

                # Read last N lines of log file
                with open(log_path) as f:
                    lines = f.readlines()
                    # Return last 50 lines (configurable via max_error_output_size)
                    max_lines = (
                        self.config.max_error_output_size // 100
                    )  # Rough estimate
                    stderr_lines = (
                        lines[-max_lines:] if len(lines) > max_lines else lines
                    )
                    return "".join(stderr_lines)

            return None

        except Exception as e:
            self._logger.warning(f"Could not capture stderr for {process_id}: {e}")
            return None
