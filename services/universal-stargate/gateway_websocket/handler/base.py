"""Base handler protocol for WebSocket message dispatch."""

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from .context import HandlerContext


@runtime_checkable
class MessageHandler(Protocol):
    """Protocol for message handlers."""

    async def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """
        Handle a WebSocket message.

        Args:
            data: Message payload
            ctx: Handler context with state accessors and side-effect schedulers
        """
        ...


class SyncMessageHandler(ABC):
    """
    Base class for synchronous (non-blocking) message handlers.

    Use for handlers that perform pure state mutations and callback scheduling.
    NO I/O allowed - use AsyncMessageHandler for that.

    Contract Requirements:
    ----------------------

    **Methods (Required):**

    - `handle()` - Sync message processing (no await, no I/O)

    Invariants:
    -----------

    - ∀ handle(): ¬∃ await ∧ ¬∃ I/O
    - Pure state mutation + callback scheduling only
    - Must complete quickly (< 1ms typical)

    When to Use:
    ------------

    Use SyncMessageHandler when:
    - Updating in-memory state
    - Scheduling async callbacks for later
    - Computing derived values
    - No I/O required

    Example:
    --------

    ```python
    class ModelStatusHandler(SyncMessageHandler):
        '''Update model status in gateway state.'''

        def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
            model_id = data["model_id"]
            status = data["status"]

            # Pure state update (no I/O)
            ctx.update_model_status(model_id, status)

            # Schedule async side-effect for later
            ctx.schedule_callback(self._notify_watchers, model_id)
    ```

    Anti-Patterns:
    --------------

    ```python
    # ❌ Async operation in sync handler
    def handle(self, data, ctx):
        await ctx.send_message(...)  # WRONG - can't await in sync

    # ❌ Blocking I/O
    def handle(self, data, ctx):
        with open("log.txt", "a") as f:  # WRONG - I/O in sync handler
            f.write(str(data))
    ```

    See Also:
    ---------
    - `AsyncMessageHandler` - For I/O handlers
    - `HandlerContext` - Provides state accessors
    """

    @abstractmethod
    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """
        Handle message synchronously (no await, no I/O).

        Args:
            data: Message payload dict
            ctx: Handler context for state access and callback scheduling

        Returns:
            None. Side effects via ctx state mutators.

        Important:
            - NO await statements allowed
            - NO I/O operations (file, network, database)
            - Must complete quickly (< 1ms)
            - Use ctx.schedule_callback() for async follow-up
        """
        ...


class AsyncMessageHandler(ABC):
    """
    Base class for async message handlers (requires I/O).

    Use for handlers that MUST perform I/O (e.g., PING→PONG, database queries).
    Prefer SyncMessageHandler for pure state mutations.

    Contract Requirements:
    ----------------------

    **Methods (Required):**

    - `handle()` - Async message processing

    Invariants:
    -----------

    - ∀ handle(): may await ∧ may perform I/O
    - Handler called once per matching message
    - Must not block event loop (use async I/O only)

    When to Use:
    ------------

    Use AsyncMessageHandler when:
    - Sending WebSocket responses (PING → PONG)
    - Making HTTP requests
    - Database queries
    - File I/O

    Use SyncMessageHandler when:
    - Pure state updates
    - Scheduling callbacks
    - No I/O required

    Example:
    --------

    ```python
    class PingHandler(AsyncMessageHandler):
        '''Respond to PING with PONG.'''

        async def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
            # Send PONG response (I/O operation)
            await ctx.send_message({
                "type": "PONG",
                "timestamp": time.time()
            })
    ```

    Anti-Patterns:
    --------------

    ```python
    # ❌ Blocking I/O
    def handle(self, data, ctx):
        requests.get("http://...")  # WRONG - blocks event loop

    # ❌ Long-running sync operation
    async def handle(self, data, ctx):
        result = expensive_cpu_computation()  # WRONG - use run_in_executor
    ```

    Registration:
    -------------

    Handlers registered in `gateway_websocket/handler/registry.py`:

    ```python
    HANDLERS: dict[str, type[MessageHandler]] = {
        "PING": PingHandler,
        "MODEL_LOADED": ModelLoadedHandler,
        ...
    }
    ```

    See Also:
    ---------
    - `SyncMessageHandler` - For non-I/O handlers
    - `MessageHandler` (Protocol) - Duck-typing interface
    - `HandlerContext` - Provides state accessors and side-effect schedulers
    """

    @abstractmethod
    async def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        """
        Handle a WebSocket message asynchronously.

        Args:
            data: Message payload dict. Always contains:
                - "type": Message type string
                - Other fields depend on message type

            ctx: Handler context providing:
                - State accessors (gateway info, model state)
                - Side-effect schedulers (send_message, emit_event)
                - Logging utilities

        Returns:
            None. Side effects via ctx methods.

        Raises:
            Any exception: Logged and handled by dispatcher.
            Handler exceptions don't crash the WebSocket connection.

        Note:
            - All I/O must use async/await
            - Don't block the event loop
            - Use ctx.send_message() for responses
            - Use ctx.emit_event() for event bus notifications
        """
        ...
