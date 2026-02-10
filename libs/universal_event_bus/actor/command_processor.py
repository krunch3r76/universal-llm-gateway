"""
Command Processor for lock-free sequential state management.

This module provides the CommandProcessor pattern that eliminates the need
for asyncio.Lock by ensuring all state mutations are processed sequentially
through an internal command queue.

Design Principles:
    1. Single-writer: Only CommandProcessor writes to managed state
    2. Sequential processing: Commands processed one at a time (no interleaving)
    3. Request-response: Callers await command completion
    4. Lock-free: No locks needed - sequentiality guarantees atomicity

Usage Pattern:
    class MyManager(CommandProcessor[MyState]):
        async def _process_command(self, command: Command) -> CommandResult:
            match command.name:
                case "reserve":
                    # Multi-step operation - no lock needed!
                    metrics = await self._fetch_metrics()
                    if self._state.has_capacity(metrics):
                        self._state = self._state.with_reservation(...)
                        return CommandResult(success=True, data=reservation)
                    return CommandResult(success=False, error="no capacity")

    # Caller:
    result = await manager.execute(Command("reserve", {"model_id": "x"}))

Why This Works:
    In Python asyncio, context switches occur ONLY at await points.
    By routing all state mutations through a single queue, we ensure:
    - Only one command executes at a time
    - No concurrent access to state
    - Multi-step operations are atomic (no interleaving)
    - Awaits within command processing are safe (queue blocks other commands)
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from universal_logging import get_logger

logger = get_logger(__name__)

# Type variable for state
S = TypeVar("S")


@dataclass(frozen=True)
class Command:
    """
    Immutable command to be processed.

    Commands are queued and processed sequentially by CommandProcessor.
    """

    name: str
    params: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


@dataclass
class CommandResult:
    """
    Result of command execution.

    Returned to the caller after command completes.
    """

    success: bool
    data: Any = None
    error: str | None = None


class CommandProcessor(Generic[S]):
    """
    Base class for lock-free sequential state management.

    Subclass this and implement _process_command() to handle commands.
    All state mutations go through execute() which ensures sequential processing.

    Type parameter S is the state type managed by this processor.
    State should be immutable (frozen dataclass) or carefully managed.
    """

    def __init__(self, initial_state: S | None = None):
        """
        Initialize command processor.

        Args:
            initial_state: Initial state value (optional)
        """
        self._state: S | None = initial_state
        self._command_queue: (
            asyncio.Queue[tuple[Command, asyncio.Future[CommandResult]]] | None
        ) = None
        self._processor_task: asyncio.Task | None = None
        self._running = False

    @property
    def state(self) -> S | None:
        """
        Get current state snapshot (read-only access).

        For thread-safe reads, state should be immutable.
        """
        return self._state

    async def start(self) -> None:
        """Start the command processor loop."""
        if self._running:
            return

        self._running = True
        if self._command_queue is None:
            self._command_queue = asyncio.Queue()
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.debug(f"{self.__class__.__name__} command processor started")

    async def stop(self) -> None:
        """
        Stop the command processor gracefully.

        Waits for current command to complete, then stops.
        """
        if not self._running:
            return

        self._running = False

        if self._command_queue is not None:
            # Put sentinel to unblock queue
            sentinel_future: asyncio.Future[CommandResult] = (
                asyncio.get_event_loop().create_future()
            )
            sentinel_future.set_result(CommandResult(success=False, error="shutdown"))
            await self._command_queue.put((Command("__shutdown__"), sentinel_future))

        if self._processor_task:
            try:
                await asyncio.wait_for(self._processor_task, timeout=5.0)
            except TimeoutError:
                logger.warning(
                    f"{self.__class__.__name__} processor did not stop gracefully"
                )
                self._processor_task.cancel()

    async def execute(self, command: Command) -> CommandResult:
        """
        Execute a command and wait for result.

        Commands are queued and processed sequentially.
        This method blocks until the command completes.

        Args:
            command: Command to execute

        Returns:
            CommandResult with success status and data/error
        """
        if not self._running or self._command_queue is None:
            return CommandResult(success=False, error="processor not running")

        # Create future for result
        result_future: asyncio.Future[CommandResult] = (
            asyncio.get_event_loop().create_future()
        )

        # Queue command
        await self._command_queue.put((command, result_future))

        # Wait for result
        return await result_future

    def execute_nowait(self, command: Command) -> None:
        """
        Execute a command without waiting for result (fire-and-forget).

        Useful for non-critical updates where caller doesn't need response.

        Args:
            command: Command to execute
        """
        if not self._running or self._command_queue is None:
            logger.warning(f"Dropping command {command.name}: processor not running")
            return

        # Create dummy future (result discarded)
        result_future: asyncio.Future[CommandResult] = (
            asyncio.get_event_loop().create_future()
        )

        def log_error(f: asyncio.Future):
            try:
                result = f.result()
                if not result.success:
                    logger.warning(
                        f"Background command {command.name} failed: {result.error}"
                    )
            except Exception as e:
                logger.error(f"Background command {command.name} error: {e}")

        result_future.add_done_callback(log_error)

        # Queue command (non-blocking put for sync context)
        try:
            self._command_queue.put_nowait((command, result_future))
        except asyncio.QueueFull:
            logger.error(f"Command queue full, dropping {command.name}")

    async def _process_loop(self) -> None:
        """
        Main processing loop - processes commands sequentially.

        This is the core of the lock-free pattern:
        - Only one command is processed at a time
        - Awaits within command processing are safe
        - No concurrent state access possible
        """
        if self._command_queue is None:
            logger.error("Command queue not initialized")
            return

        while self._running:
            try:
                # Wait for next command
                command, result_future = await self._command_queue.get()

                # Check for shutdown sentinel
                if command.name == "__shutdown__":
                    break

                # Process command
                try:
                    result = await self._process_command(command)
                    result_future.set_result(result)
                except Exception as e:
                    logger.error(f"Command {command.name} failed: {e}", exc_info=True)
                    result_future.set_result(CommandResult(success=False, error=str(e)))

                self._command_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Command processor error: {e}", exc_info=True)

    async def _process_command(self, command: Command) -> CommandResult:
        """
        Process a single command. Override in subclass.

        This method can contain any number of await statements.
        Atomicity is guaranteed by sequential processing.

        Args:
            command: Command to process

        Returns:
            CommandResult with success status and data/error
        """
        raise NotImplementedError("Subclass must implement _process_command")
