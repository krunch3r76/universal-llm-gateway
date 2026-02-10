"""
Worker process base class implementation.

Provides a base class for worker processes that participate in the IPC system.
Handles standard message types and provides hooks for custom implementation.
"""

import asyncio
import signal
import sys
from abc import abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any

from universal_logging import get_logger
from universal_transport import AsyncUnixServer, ProcessIPCCompatibleServer
from universal_transport.core.interfaces import Transport
from universal_transport.core.message_pump import MessagePump

from ..core import signals
from ..core.config import UnixSocketConfig
from ..core.exceptions import ProcessError, WorkerError
from ..core.interfaces import WorkerInterface
from ..core.messages import (
    create_message,
    get_correlation_id,
    validate_message,
)
from ..core.types import ProcessState
from ..core.worker_state import WorkerStateReporter


class CommandProcessor:
    """Isolated command processing logic."""

    def __init__(
        self,
        worker_id: str,
        handler: Callable,
        active_commands: set[str],
        cancellation_queue: asyncio.Queue,
    ):
        self._worker_id = worker_id
        self._handler = handler
        self._active_commands = active_commands
        self._cancellation_queue = cancellation_queue
        self._logger = get_logger(f"worker.{worker_id}.commands")

    async def _execute_with_yields(
        self, handler: Callable, command_data: dict[str, Any], correlation_id: str
    ) -> dict[str, Any]:
        """
        Execute handler with periodic event loop yields for cancellation responsiveness.

        This method wraps the actual command handler and periodically yields control
        to the event loop every 100ms. This ensures that cancellation commands
        received during long-running operations (like streaming inference) are
        processed quickly rather than waiting for the entire operation to complete.

        Args:
            handler: The command handler function to execute
            command_data: Command data to pass to handler
            correlation_id: Correlation ID for this command

        Returns:
            Result from the handler
        """
        handler_task = asyncio.create_task(handler(command_data))

        while not handler_task.done():
            try:
                # Wait with 100ms timeout, shield the task from cancellation here
                result = await asyncio.wait_for(
                    asyncio.shield(handler_task), timeout=0.1
                )
                return result
            except TimeoutError:
                # Timeout is expected - yield to event loop to process incoming messages
                await asyncio.sleep(0)
                continue

        # Task completed, return result
        return await handler_task

    async def process(self, message: dict[str, Any]) -> dict[str, Any]:
        """Process command with automatic event loop yielding."""
        correlation_id = get_correlation_id(message)
        command_data = message.get("payload", {})

        if not correlation_id:
            return create_message(
                signal=signals.ERROR,
                payload={"error": "Missing correlation_id in command"},
                worker_id=self._worker_id,
            )

        # Track active command
        self._active_commands.add(correlation_id)

        try:
            # Execute handler with automatic yielding for cancellation responsiveness
            result = await self._execute_with_yields(
                self._handler, command_data, correlation_id
            )

            # Validate result
            if result is None:
                self._logger.warning(
                    f"Handler returned None for correlation_id: {correlation_id}. "
                    f"Command data keys: {list(command_data.keys())}"
                )
                result = {"error": "Handler returned empty result"}
            elif not isinstance(result, dict):
                # Convert non-dict result to dict
                result = {"result": result}
            elif not result:
                # Empty dict result
                self._logger.warning(
                    f"Handler returned empty dict for correlation_id: {correlation_id}. "
                    f"Command data keys: {list(command_data.keys())}"
                )
                result = {"error": "Handler returned empty result"}
            else:
                # Check if result only contains command metadata (indicates handler returned command_data)
                command_metadata_keys = {"command_type", "worker_id", "correlation_id"}
                result_keys = set(result.keys())
                # If all result keys are metadata keys, this is suspicious
                if result_keys and result_keys.issubset(command_metadata_keys):
                    # Result only contains command metadata - handler likely returned command_data unchanged
                    self._logger.error(
                        f"Handler result only contains command metadata for correlation_id: {correlation_id}. "
                        f"Result keys: {list(result_keys)}. "
                        f"This indicates process_command() returned command_data instead of processing it."
                    )
                    result = {
                        "error": "Handler returned command metadata only - implementation error"
                    }

            # Log result structure for debugging
            self._logger.debug(
                f"Command response result for correlation_id {correlation_id}: "
                f"keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}, "
                f"result_type={type(result).__name__}"
            )

            # Simple UML Message format: payload contains worker response directly (no result wrapper, no metadata)
            response = signals.CommandComplete(
                result=result,
                correlation_id=correlation_id,
                worker_id=self._worker_id,
            )

            # Verify response structure before returning
            response_payload = response.get("payload", {})
            self._logger.debug(
                f"Command response created: signal={response['signal']}, "
                f"payload_keys={list(response_payload.keys())}, "
                f"correlation_id={correlation_id}"
            )

            return response
        except Exception as e:
            self._logger.error(f"Command failed: {e}", exc_info=True)
            # Simple UML Message format: payload contains error directly (no metadata)
            return signals.CommandError(
                worker_id=self._worker_id,
                error=str(e),
                correlation_id=correlation_id,
            )
        finally:
            # Remove from active commands
            self._active_commands.discard(correlation_id)


def log_process_event(
    logger, worker_id: str, event: str, level: str = "INFO", **kwargs
):
    """
    Simple logging function to replace the missing log_process_event.

    Args:
        logger: Logger instance
        worker_id: Worker identifier
        event: Event name
        level: Log level (INFO, ERROR, DEBUG, etc.)
        **kwargs: Additional context to log
    """
    message = f"[{worker_id}] {event}"
    if kwargs:
        context = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
        message += f" ({context})"

    if level.upper() == "ERROR":
        logger.error(message)
    elif level.upper() == "DEBUG":
        logger.debug(message)
    elif level.upper() == "WARNING":
        logger.warning(message)
    else:
        logger.info(message)


class WorkerProcess(WorkerInterface):
    """
    Base class for worker processes that participate in the IPC system.

    Provides standard message handling and lifecycle management.
    Subclasses should implement the abstract methods for custom logic.
    """

    def __init__(
        self,
        worker_id: str,
        socket_path: str,
        message_pump_receive_timeout: float | None = 30.0,
    ):
        """
        Initialize worker process.

        Args:
            worker_id: Unique identifier for this worker
            socket_path: Path to Unix socket for IPC communication
            message_pump_receive_timeout: Timeout for MessagePump transport.receive() calls in seconds.
                                         Prevents indefinite hangs from incomplete socket data.
                                         Set to None to disable timeout (not recommended).
                                         Default: 30.0 seconds.
        """
        self.worker_id = worker_id
        self.socket_path = socket_path
        self.message_pump_receive_timeout = message_pump_receive_timeout
        self._transport: Transport | None = None
        self._server: ProcessIPCCompatibleServer | None = None
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._logger = get_logger("process_ipc.process.worker")

        # Message pump for concurrent I/O
        self._message_pump: MessagePump | None = None
        self._message_task: asyncio.Task | None = None

        # Streaming state management
        self._stream_cancellation_events: dict[str, asyncio.Event] = {}

        # Cancellation request queue for fast cancellation delivery
        self._cancellation_requests: asyncio.Queue[str] = asyncio.Queue()

        # Status tracking for long-running operations
        self._status: dict[str, Any] = {
            "model_loaded": False,
            "initialized": False,
            "ready": False,
        }

        # Active command tracking for status
        self._active_commands: set[str] = set()

        # Message handlers - pure event-driven only
        # Using signals from core.signals
        self._message_handlers: dict[str, Callable] = {
            signals.HEALTH_CHECK: self._handle_health,
            signals.SHUTDOWN: self._handle_shutdown,
            signals.COMMAND: self._handle_command,
            signals.STREAM_START: self._handle_stream_start,
            signals.STREAM_CHUNK: self._handle_stream_chunk,
            signals.STREAM_END: self._handle_stream_end,
            signals.CANCEL_STREAM: self._handle_cancel_stream,
            signals.DATA_STREAM: self._handle_data_stream,
        }

        # Command processor (created after handlers are set up)
        self._command_processor = CommandProcessor(
            worker_id,
            self.process_command,
            self._active_commands,
            self._cancellation_requests,
        )
        self._message_handlers[signals.COMMAND] = self._command_processor.process

        # State reporting support
        self._state_reporter: WorkerStateReporter | None = None

        log_process_event(
            self._logger, worker_id, "worker_initialized", socket_path=socket_path
        )

    async def initialize(self, socket_path: str) -> None:
        """
        Initialize the worker process and establish IPC connection.

        Args:
            socket_path: Unix socket path for IPC communication

        Raises:
            ConnectionError: If IPC connection fails
        """
        if socket_path:
            self.socket_path = socket_path

        try:
            # Create AsyncUnixServer and wrap with ProcessIPCCompatibleServer
            unix_server = AsyncUnixServer(
                socket_path=self.socket_path,
                max_clients=1,  # Single worker connection
            )
            self._server = ProcessIPCCompatibleServer(unix_server)

            # Start server and wait for connection FIRST (before loading model)
            log_process_event(
                self._logger,
                self.worker_id,
                "starting_socket_server",
                socket_path=self.socket_path,
            )

            # Get Transport interface for single-client mode (from generic AsyncTransportServer)
            self._transport = await self._server.get_transport(timeout=30.0)

            # Create message pump with configured receive timeout
            # MessagePump(transport, get_correlation_id=None, receive_timeout=30.0)
            self._message_pump = MessagePump(
                self._transport, receive_timeout=self.message_pump_receive_timeout
            )

            log_process_event(
                self._logger,
                self.worker_id,
                "socket_server_started",
                socket_path=self.socket_path,
            )

            # Initialize state reporter after transport is connected
            await self._initialize_state_reporter()

            # Report initial state
            await self.report_state(ProcessState.STARTING)

            # Don't send ready message here - wait until after _initialize_worker()
            # Ready message will be sent from main() after initialization completes

            # NOW initialize worker-specific resources (load model)
            log_process_event(
                self._logger, self.worker_id, "starting_worker_initialization"
            )

            # Report that we're initializing
            await self.report_state(ProcessState.INITIALIZING)

            await self._initialize_worker()

            # Update status after worker initialization
            self._status["initialized"] = True

            # Report that we're ready
            await self.report_state(ProcessState.READY)

            log_process_event(
                self._logger, self.worker_id, "worker_initialization_completed"
            )

            log_process_event(
                self._logger,
                self.worker_id,
                "worker_initialized_and_ready",
                socket_path=self.socket_path,
                status=self._status,
            )

        except Exception as e:
            log_process_event(
                self._logger,
                self.worker_id,
                "worker_initialization_failed",
                level="ERROR",
                error=str(e),
            )
            raise WorkerError(f"Failed to initialize worker: {e}", self.worker_id)

    async def _initialize_worker(self) -> None:
        """
        Initialize worker-specific resources.

        This method should be overridden by subclasses to perform
        worker-specific initialization (e.g., loading models).
        """
        # Base implementation does nothing
        # Subclasses should override this and update self._status as needed
        pass

    async def _send_ready_message(self) -> None:
        """Send ready message to indicate worker is ready."""
        # Update ready status
        self._status["ready"] = True

        # Add worker-specific ready information
        ready_info = await self._get_ready_info()

        # Create message with factory function
        ready_message = signals.Ready(
            worker_id=self.worker_id,
            status="loaded",
            worker_status=self._status.copy(),
            **ready_info,
        )

        # Add small delay to ensure connection is stable
        await asyncio.sleep(0.1)

        await self._transport.send(ready_message)

        log_process_event(
            self._logger,
            self.worker_id,
            "ready_message_sent",
            ready_info=ready_info,
            status=self._status,
        )

    async def _get_ready_info(self) -> dict[str, Any]:
        """
        Get worker-specific ready information.

        Returns:
            Dict[str, Any]: Additional information to include in ready message
        """
        return {}

    async def run(self) -> None:
        """
        Enhanced run method with concurrent message processing.

        Creates separate tasks for:
        1. Socket reading (continuous)
        2. Message processing (concurrent)
        3. Streaming operations (concurrent)

        This is the main entry point for the worker process.
        """
        self._running = True

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._shutdown_event.set())

        log_process_event(self._logger, self.worker_id, "worker_starting")

        try:
            # Start message pump
            if self._message_pump:
                await self._message_pump.start()

            # Start message processing task
            self._message_task = asyncio.create_task(self._process_messages())

            log_process_event(self._logger, self.worker_id, "worker_started")

            # Wait for shutdown signal
            await self._shutdown_event.wait()

            log_process_event(self._logger, self.worker_id, "worker_stopping")

        except Exception as e:
            log_process_event(
                self._logger,
                self.worker_id,
                "worker_error",
                level="ERROR",
                error=str(e),
            )
            raise WorkerError(f"Worker error: {e}", self.worker_id)

        finally:
            await self.shutdown()

    async def _process_messages(self) -> None:
        """Process messages from the pump concurrently."""
        if not self._message_pump:
            return

        try:
            while self._running and not self._shutdown_event.is_set():
                try:
                    # Get message from pump using get_message() which reads from
                    # the internal queue. This is safe to call when the pump is running
                    # because it doesn't conflict with the pump's exclusive receive loop.
                    # Returns None on timeout, so we check for message before processing.
                    message = await self._message_pump.get_message(timeout=1.0)

                    if message:
                        # Process message concurrently
                        asyncio.create_task(self._process_message(message))

                except Exception as e:
                    log_process_event(
                        self._logger,
                        self.worker_id,
                        "message_processing_error",
                        level="ERROR",
                        error=str(e),
                    )
                    continue

        except asyncio.CancelledError:
            log_process_event(
                self._logger,
                self.worker_id,
                "message_processing_cancelled",
                level="DEBUG",
            )
        except Exception as e:
            log_process_event(
                self._logger,
                self.worker_id,
                "message_processing_fatal_error",
                level="ERROR",
                error=str(e),
            )

    async def _process_message(self, message: dict[str, Any]) -> None:
        """Process an incoming message."""
        # Validate message structure
        try:
            validate_message(message)
        except Exception as e:
            self._logger.error(f"Invalid message structure: {e}")
            return

        signal = message.get("signal")
        correlation_id = get_correlation_id(message)

        log_process_event(
            self._logger,
            self.worker_id,
            "message_received",
            level="DEBUG",
            signal=signal,
            correlation_id=correlation_id,
        )

        # Find handler for signal
        handler = self._message_handlers.get(signal)

        if handler:
            try:
                response = await handler(message)

                # Send response if one was generated
                if response:
                    # Add correlation ID to response if not already present
                    if correlation_id and "correlation_id" not in response:
                        response["correlation_id"] = correlation_id

                    await self._transport.send(response)

                    log_process_event(
                        self._logger,
                        self.worker_id,
                        "response_sent",
                        level="DEBUG",
                        signal=signal,
                        response_signal=response.get("signal"),
                        correlation_id=correlation_id,
                    )

            except Exception as e:
                log_process_event(
                    self._logger,
                    self.worker_id,
                    "message_handler_error",
                    level="ERROR",
                    signal=signal,
                    error=str(e),
                )

                # Send error response
                error_response = signals.Error(
                    worker_id=self.worker_id,
                    error=str(e),
                    original_signal=signal,
                    correlation_id=correlation_id,
                )

                await self._transport.send(error_response)
        else:
            log_process_event(
                self._logger,
                self.worker_id,
                "unknown_signal",
                level="WARNING",
                signal=signal,
            )

            # Send error response for unknown signal
            error_response = create_message(
                signal=signals.ERROR,
                payload={
                    "worker_id": self.worker_id,
                    "error": f"Unknown signal: {signal}",
                    "original_signal": signal,
                },
                correlation_id=correlation_id,
                worker_id=self.worker_id,
            )

            await self._transport.send(error_response)

    async def _handle_health(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle health check message."""
        health_info = await self.health_check()
        correlation_id = get_correlation_id(message)

        return signals.HealthResponse(
            worker_id=self.worker_id,
            status="healthy"
            if health_info.get("healthy", True)
            else "unhealthy",
            healthy=health_info.get("healthy", True),
            details=health_info,
            correlation_id=correlation_id,
        )

    async def _handle_shutdown(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle shutdown message."""
        log_process_event(self._logger, self.worker_id, "shutdown_requested")

        # Set shutdown event to stop the main loop
        self._shutdown_event.set()

        correlation_id = get_correlation_id(message)

        # Send immediate acknowledgment
        ack_response = signals.ShutdownAck(
            worker_id=self.worker_id,
            status="acknowledged",
            correlation_id=correlation_id,
        )

        log_process_event(self._logger, self.worker_id, "shutdown_ack_sent")

        return ack_response

    async def _handle_command(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle commands by delegating to CommandProcessor."""
        return await self._command_processor.process(message)

    def get_status(self) -> dict[str, Any]:
        """Get current worker status."""
        return {
            **self._status,
            "active_commands": len(self._active_commands),
            "message_queue_size": self._message_pump.get_queue_size()
            if self._message_pump
            else 0,
        }

    def is_ready_for_inference(self) -> bool:
        """Check if worker is ready to handle inference requests."""
        return (
            self._status.get("model_loaded", False)
            and self._status.get("initialized", False)
            and self._running
            and len(self._active_commands) < 10  # Reasonable concurrency limit
        )

    async def emit_event(self, event_type: str, event_data: dict[str, Any]):
        """
        Emit event to manager.

        Args:
            event_type: Type of event (used as signal name)
            event_data: Event data - for streaming events, should contain domain data directly
        """
        # Extract correlation_id if present (will be moved to top level)
        correlation_id = event_data.get("correlation_id")

        # For streaming events, remove worker_id and redundant correlation_id from payload
        # (they're already at the top level of the message)
        streaming_signals = {
            signals.DATA_STREAM,
            signals.STREAM_CHUNK,
            signals.STREAM_START,
            signals.STREAM_END,
            signals.STREAM_ERROR,
        }

        if event_type in streaming_signals:
            # Remove metadata fields from payload for streaming events
            payload = {
                k: v
                for k, v in event_data.items()
                if k not in {"worker_id", "correlation_id"}
            }
        else:
            # For non-streaming events, ensure worker_id is in payload (for backward compatibility)
            if "worker_id" not in event_data:
                payload = dict(event_data)
                payload["worker_id"] = self.worker_id
            else:
                payload = event_data

        # Create properly structured message
        event_message = create_message(
            signal=event_type,
            payload=payload,
            correlation_id=correlation_id,
            worker_id=self.worker_id,
        )

        log_process_event(
            self._logger,
            self.worker_id,
            "event_emitted",
            level="DEBUG",
            event_type=event_type,
            correlation_id=correlation_id,
        )

        # Send to manager with timeout to prevent deadlock
        if self._transport:
            try:
                # CRITICAL: Add timeout to prevent indefinite blocking
                # If gateway is not consuming messages fast enough, socket buffer fills
                # and drain() will block forever, causing deadlock
                await asyncio.wait_for(
                    self._transport.send(event_message), timeout=30.0
                )
            except TimeoutError:
                error_msg = (
                    f"Timeout sending event {event_type} with correlation_id {correlation_id}. "
                    f"Gateway may be blocked or not consuming messages fast enough. "
                    f"Socket buffer likely full."
                )
                self._logger.error(error_msg)
                # Propagate error to caller. Silently swallowing this leads to data loss.
                raise ProcessError(error_msg, self.worker_id)

    async def report_streaming_chunk(
        self, correlation_id: str, chunk_data: dict[str, Any], chunk_number: int
    ) -> None:
        """
        Report individual streaming chunk.

        Args:
            correlation_id: Correlation ID for the streaming operation
            chunk_data: Data for this chunk
            chunk_number: Sequential chunk number (1-based)
        """
        event_data = {
            "worker_id": self.worker_id,
            "correlation_id": correlation_id,
            "chunk_number": chunk_number,
            "chunk_data": chunk_data,
        }

        await self.emit_event(signals.STREAM_CHUNK, event_data)

        log_process_event(
            self._logger,
            self.worker_id,
            "streaming_chunk_reported",
            level="DEBUG",
            correlation_id=correlation_id,
            chunk_number=chunk_number,
        )

    async def report_streaming_complete(
        self, correlation_id: str, total_chunks: int
    ) -> None:
        """
        Report streaming completion.

        Args:
            correlation_id: Correlation ID for the streaming operation
            total_chunks: Total number of chunks sent
        """
        event_data = {
            "worker_id": self.worker_id,
            "correlation_id": correlation_id,
            "total_chunks": total_chunks,
        }

        await self.emit_event(signals.STREAM_END, event_data)

        log_process_event(
            self._logger,
            self.worker_id,
            "streaming_complete_reported",
            level="DEBUG",
            correlation_id=correlation_id,
            total_chunks=total_chunks,
        )

    async def report_streaming_error(self, correlation_id: str, error: str) -> None:
        """
        Report streaming error.

        Args:
            correlation_id: Correlation ID for the streaming operation
            error: Error message
        """
        event_data = {
            "worker_id": self.worker_id,
            "correlation_id": correlation_id,
            "error": error,
        }

        await self.emit_event(signals.STREAM_ERROR, event_data)

        log_process_event(
            self._logger,
            self.worker_id,
            "streaming_error_reported",
            level="ERROR",
            correlation_id=correlation_id,
            error=error,
        )

    async def check_cancellation(self, correlation_id: str) -> bool:
        """
        Check if cancellation has been requested for a correlation ID.

        Custom workers should call this periodically during long operations
        to enable responsive cancellation. This is particularly useful for
        streaming inference or other long-running operations that need to
        respond quickly to cancellation requests.

        Args:
            correlation_id: Correlation ID to check for cancellation

        Returns:
            bool: True if cancellation has been requested, False otherwise

        Example:
            async def stream_inference(self, command):
                correlation_id = command["correlation_id"]

                # Register cancellation event for this operation
                self._stream_cancellation_events[correlation_id] = asyncio.Event()

                try:
                    for chunk in generate_chunks():
                        # Check for cancellation every iteration
                        if await self.check_cancellation(correlation_id):
                            self._logger.info(f"Stream {correlation_id} cancelled")
                            break

                        # Send chunk
                        await self.emit_chunk(chunk, correlation_id)
                finally:
                    # Cleanup
                    if correlation_id in self._stream_cancellation_events:
                        del self._stream_cancellation_events[correlation_id]
        """
        if correlation_id in self._stream_cancellation_events:
            return self._stream_cancellation_events[correlation_id].is_set()
        return False

    def add_message_handler(self, message_type: str, handler: Callable) -> None:
        """
        Add a custom message handler.

        Args:
            message_type: Type of message to handle
            handler: Async function to handle the message
        """
        self._message_handlers[message_type] = handler

        log_process_event(
            self._logger,
            self.worker_id,
            "message_handler_added",
            message_type=message_type,
        )

    async def _handle_stream_start(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Handle stream start command.

        Creates a new stream and starts processing it concurrently.
        """
        correlation_id = get_correlation_id(message)
        payload = message.get("payload", {})

        # Create stream cancellation event
        self._stream_cancellation_events[correlation_id] = asyncio.Event()

        log_process_event(
            self._logger,
            self.worker_id,
            "stream_started",
            correlation_id=correlation_id,
        )

        # Start streaming task
        asyncio.create_task(self._stream_data(correlation_id, payload))

        return signals.StreamStarted(
            status="started",
            correlation_id=correlation_id,
            worker_id=self.worker_id,
        )

    async def _handle_stream_chunk(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Handle stream chunk message."""
        # This is typically used when receiving chunks from another component
        # In this implementation, we're focusing on sending chunks
        return None

    async def _handle_stream_end(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Handle stream end message."""
        # This is typically used when receiving end signal from another component
        return None

    async def _handle_data_stream(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Handle efficient single-message data transfer.

        This handler receives data sent via the send_data() method, which sends
        the entire payload in one message without artificial chunking.

        Override this method in your worker subclass to process incoming data.
        """
        correlation_id = get_correlation_id(message)
        payload = message.get("payload", {})
        data = payload.get("data")

        log_process_event(
            self._logger,
            self.worker_id,
            "data_received",
            level="DEBUG",
            correlation_id=correlation_id,
            data_size=len(str(data)) if data else 0,
        )

        # Default implementation: call process_data if it exists
        if hasattr(self, "process_data"):
            try:
                result = await self.process_data(data, correlation_id)
                # Simple UML Message format: payload contains worker response directly
                return signals.CommandComplete(
                    result=result if isinstance(result, dict) else {"result": result},
                    correlation_id=correlation_id,
                    worker_id=self.worker_id,
                )
            except Exception as e:
                self._logger.error(f"Error processing data: {e}", exc_info=True)
                # Simple UML Message format: payload contains error directly
                return signals.Error(
                    worker_id=self.worker_id,
                    error=str(e),
                    original_signal=signals.DATA_STREAM,
                    correlation_id=correlation_id,
                )

        # If no process_data method exists, just acknowledge receipt
        return None

    async def _handle_cancel_stream(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Handle stream cancellation command with immediate delivery.

        Sets the cancellation event for the specified stream and queues
        the cancellation request for immediate processing during command execution.
        """
        correlation_id = get_correlation_id(message)

        # Signal cancellation via event (existing mechanism)
        if correlation_id in self._stream_cancellation_events:
            self._stream_cancellation_events[correlation_id].set()

            # NEW: Also queue for immediate delivery during command processing
            await self._cancellation_requests.put(correlation_id)

            log_process_event(
                self._logger,
                self.worker_id,
                "stream_cancelled",
                correlation_id=correlation_id,
            )

            return signals.StreamCancelled(
                status="cancelled",
                correlation_id=correlation_id,
                worker_id=self.worker_id,
            )
        else:
            return signals.Error(
                worker_id=self.worker_id,
                error="Stream not found",
                original_signal=signals.CANCEL_STREAM,
                correlation_id=correlation_id,
            )

    async def _stream_data(self, correlation_id: str, payload: dict[str, Any]) -> None:
        """
        Stream data chunks while checking for cancellation.

        This runs concurrently with other message processing.
        """
        try:
            chunk_count = 0
            max_chunks = payload.get("max_chunks", 100)

            while chunk_count < max_chunks:
                # Check for cancellation
                if correlation_id in self._stream_cancellation_events:
                    if self._stream_cancellation_events[correlation_id].is_set():
                        log_process_event(
                            self._logger,
                            self.worker_id,
                            "stream_cancellation_detected",
                            correlation_id=correlation_id,
                            chunk_count=chunk_count,
                        )
                        break

                # Simulate streaming work
                await asyncio.sleep(0.1)  # Simulate processing time

                # Send chunk
                chunk_message = signals.StreamChunk(
                    chunk_id=chunk_count,
                    data=f"chunk_{chunk_count}",
                    total_chunks=max_chunks,
                    correlation_id=correlation_id,
                    worker_id=self.worker_id,
                )

                await self._transport.send(chunk_message)
                chunk_count += 1

            # Send stream end
            end_message = signals.StreamEnd(
                total_chunks=chunk_count,
                status="completed",
                correlation_id=correlation_id,
                worker_id=self.worker_id,
            )

            await self._transport.send(end_message)

        except Exception as e:
            log_process_event(
                self._logger,
                self.worker_id,
                "stream_error",
                level="ERROR",
                correlation_id=correlation_id,
                error=str(e),
            )

            # Send error message
            error_message = signals.StreamError(
                error=str(e),
                chunk_count=chunk_count,
                correlation_id=correlation_id,
                worker_id=self.worker_id,
            )

            await self._transport.send(error_message)

        finally:
            # Cleanup
            if correlation_id in self._stream_cancellation_events:
                del self._stream_cancellation_events[correlation_id]

    @abstractmethod
    async def process_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """
        Process command - implemented by subclasses.

        This replaces the old process_message method for async operations.
        Commands are processed in the background and results are emitted as events.

        Args:
            command: Command data to process

        Returns:
            Dict[str, Any]: Command result data

        Raises:
            Exception: Any processing errors (will be emitted as error events)
        """
        pass

    async def health_check(self) -> dict[str, Any]:
        """
        Perform internal health check.

        Returns:
            Dict[str, Any]: Health status information
        """
        status = self.get_status()
        return {
            "healthy": True,
            "worker_id": self.worker_id,
            "timestamp": datetime.now().isoformat(),
            **status,
        }

    async def shutdown(self) -> None:
        """
        Shutdown the worker process gracefully.
        """
        if not self._running:
            return

        self._running = False

        log_process_event(self._logger, self.worker_id, "worker_shutting_down")

        # Cancel message task
        if self._message_task and not self._message_task.done():
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                log_process_event(
                    self._logger,
                    self.worker_id,
                    "message_task_cancelled",
                    level="DEBUG",
                )
            except Exception as e:
                log_process_event(
                    self._logger,
                    self.worker_id,
                    "message_task_shutdown_error",
                    level="ERROR",
                    error=str(e),
                )

        # Stop message pump
        if self._message_pump:
            await self._message_pump.stop()

        # Close transport
        if self._transport:
            try:
                await self._transport.close()
                log_process_event(self._logger, self.worker_id, "transport_closed")
            except Exception as e:
                log_process_event(
                    self._logger,
                    self.worker_id,
                    "transport_close_error",
                    level="WARNING",
                    error=str(e),
                )
            self._transport = None

        # Stop server if needed
        if self._server and self._server.is_running():
            try:
                await self._server.stop()
                log_process_event(self._logger, self.worker_id, "server_stopped")
            except Exception as e:
                log_process_event(
                    self._logger,
                    self.worker_id,
                    "server_stop_error",
                    level="WARNING",
                    error=str(e),
                )
            self._server = None

        # Cleanup worker-specific resources
        try:
            await self._cleanup_worker()
            log_process_event(self._logger, self.worker_id, "worker_cleanup_completed")
        except Exception as e:
            log_process_event(
                self._logger,
                self.worker_id,
                "worker_cleanup_error",
                level="ERROR",
                error=str(e),
            )

        log_process_event(self._logger, self.worker_id, "worker_shutdown_complete")

    async def _cleanup_worker(self) -> None:
        """
        Cleanup worker-specific resources.

        This method can be overridden by subclasses to perform
        worker-specific cleanup.
        """
        pass

    # State reporting methods
    async def _initialize_state_reporter(self) -> None:
        """Initialize the state reporter after transport is established."""
        if self._transport:
            self._state_reporter = WorkerStateReporter(self._transport, self.worker_id)
            self._logger.debug(f"State reporter initialized for {self.worker_id}")

    async def report_state(
        self, state: ProcessState, details: dict[str, Any] = None
    ) -> None:
        """Report current state to the manager."""
        if self._state_reporter:
            try:
                await self._state_reporter.report_state(state, details)
                self._logger.debug(f"Reported state: {state.value}")
            except Exception as e:
                self._logger.warning(f"Failed to report state {state.value}: {e}")
        else:
            self._logger.warning("State reporter not initialized")

    async def report_activity(
        self, activity_type: str, details: dict[str, Any] = None
    ) -> None:
        """Report current activity to the manager."""
        if self._state_reporter:
            try:
                await self._state_reporter.report_activity(activity_type, details)
                self._logger.debug(f"Reported activity: {activity_type}")
            except Exception as e:
                self._logger.warning(f"Failed to report activity {activity_type}: {e}")
        else:
            self._logger.warning("State reporter not initialized")

    async def report_progress(self, progress: float, message: str = None) -> None:
        """Report progress for long-running operations."""
        if self._state_reporter:
            try:
                await self._state_reporter.report_progress(progress, message)
                self._logger.debug(f"Reported progress: {progress:.1%}")
            except Exception as e:
                self._logger.warning(f"Failed to report progress: {e}")
        else:
            self._logger.warning("State reporter not initialized")

    def is_running(self) -> bool:
        """
        Check if worker is running.

        Returns:
            bool: True if worker is running
        """
        return self._running

    @classmethod
    def run_worker(cls, worker_id: str, socket_path: str, **kwargs) -> None:
        """
        Convenience method to run a worker process.

        Args:
            worker_id: Unique identifier for the worker
            socket_path: Path to Unix socket for IPC
            **kwargs: Additional arguments for worker initialization
        """

        async def main():
            worker = cls(worker_id, socket_path, **kwargs)
            await worker.initialize(socket_path)
            await worker.run()

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"Worker error: {e}", file=sys.stderr)
            sys.exit(1)
