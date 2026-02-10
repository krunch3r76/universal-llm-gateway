"""
Simplified telemetry sender for Edge Stargate → Relay/Master path.

Edge Stargates have bounded telemetry volume (one Gateway), so rate limiting
is unnecessary. This sender provides:
- Readiness gating (block until upstream authenticated)
- Queue clearing on disconnect (bounded memory)
- Simple async queue (no token bucket)

For aggregation points (Relay→Master), use RemoteTelemetrySender instead.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from universal_protocol.messages import MessageEnvelope

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EdgeTelemetrySender:
    """
    Simple readiness-gated sender for Edge→Relay/Master telemetry.

    Architecture:
    - Producer: `send()` enqueues immediately (never blocks)
    - Consumer: Waits for `_ready` event, then drains queue
    - Disconnect: Clears queue (no stale telemetry on reconnect)

    Race-safe disconnect:
    - _disconnecting flag prevents new sends during disconnect
    - Queue cleared atomically after _disconnecting=True

    INVARIANT: ∀ telemetry: produced ⟹
        (◇forwarded ∨ cleared ∨ rejected_during_disconnect)
    """

    def __init__(
        self,
        send_callback: Callable[[MessageEnvelope], Awaitable[bool]],
        stargate_id: str,
        max_queue_size: int = 1000,
    ):
        """
        Initialize sender.

        Args:
            send_callback: Async function to send message to upstream
            stargate_id: This Edge's stargate_id (for source rewriting)
            max_queue_size: Maximum queue size (bounded memory)
        """
        self._send_callback = send_callback
        self._stargate_id = stargate_id

        # Bounded queue (prevents memory growth even without rate limiting)
        self._queue: asyncio.Queue[MessageEnvelope] = asyncio.Queue(
            maxsize=max_queue_size
        )

        # Readiness: Signaled when upstream authenticated
        self._ready = asyncio.Event()

        # Race fix: prevent enqueue during disconnect
        self._disconnecting = False

        # Lifecycle
        self._running = False
        self._consumer_task: asyncio.Task | None = None

        # Stats
        self._enqueued_count = 0
        self._sent_count = 0
        self._dropped_count = 0
        self._cleared_count = 0

    async def start(self) -> None:
        """Start consumer worker."""
        if self._running:
            logger.warning("EdgeTelemetrySender already running")
            return

        self._running = True
        self._consumer_task = asyncio.create_task(
            self._consumer_worker(), name="edge-telemetry-consumer"
        )
        logger.info("EdgeTelemetrySender started (waiting for upstream ready)")

    async def stop(self) -> None:
        """Stop consumer worker."""
        self._running = False

        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        self._consumer_task = None

        logger.info(
            f"EdgeTelemetrySender stopped: "
            f"enqueued={self._enqueued_count}, "
            f"sent={self._sent_count}, "
            f"dropped={self._dropped_count}, "
            f"cleared={self._cleared_count}"
        )

    def signal_ready(self) -> None:
        """Signal upstream authenticated (consumer starts sending)."""
        self._ready.set()
        logger.info("✅ Upstream ready - Edge telemetry forwarding active")

    def signal_disconnected(self) -> None:
        """
        Signal upstream disconnected.

        Race-safe disconnect handling:
        1. Set _disconnecting flag (rejects new send() calls)
        2. Clear _ready (stops consumer from sending)
        3. Drain queue (clear all pending items)
        4. Reset _disconnecting (allow new sends for next connect)

        Invariant: ∀ telemetry: produced ⟹ (◇forwarded ∨ cleared ∨ rejected)
        """
        # Step 1: Block new sends (race fix)
        self._disconnecting = True

        # Step 2: Stop consumer
        self._ready.clear()

        # Step 3: Drain queue
        cleared = self._clear_queue()
        self._cleared_count += cleared

        # Step 4: Allow new sends (will queue for next connect)
        self._disconnecting = False

        if cleared > 0:
            logger.warning(
                f"⚠️ Upstream disconnected - cleared {cleared} queued telemetry items"
            )
        else:
            logger.info("⚠️ Upstream disconnected - no queued telemetry")

    @property
    def is_ready(self) -> bool:
        """Check if upstream is ready."""
        return self._ready.is_set()

    async def send(self, msg: MessageEnvelope) -> bool:
        """
        Enqueue telemetry for sending.

        Producer path: Never blocks (may drop if queue full or disconnecting).
        Consumer handles actual sending when upstream ready.

        Args:
            msg: Message envelope to send

        Returns:
            True if enqueued, False if dropped
        """
        # Race fix: reject during disconnect
        if self._disconnecting:
            self._dropped_count += 1
            # Throttled debug logging (every 10th rejection during disconnect)
            if self._dropped_count % 10 == 1:
                logger.debug(
                    f"Telemetry rejected during disconnect: {msg.type} "
                    f"(dropped={self._dropped_count})"
                )
            return False

        try:
            self._queue.put_nowait(msg)
            self._enqueued_count += 1

            # Throttled debug logging (every 50th message)
            if self._enqueued_count % 50 == 1:
                logger.debug(
                    f"Telemetry enqueued: {msg.type} "
                    f"(total={self._enqueued_count}, queue_size={self._queue.qsize()})"
                )
            return True

        except asyncio.QueueFull:
            self._dropped_count += 1
            logger.warning(
                f"Telemetry dropped (queue full): {msg.type} "
                f"(dropped={self._dropped_count})"
            )
            return False

    async def forward_gateway_telemetry(
        self,
        msg_type: str,
        data: dict[str, Any],
    ) -> bool:
        """
        Forward local Gateway telemetry to upstream.

        Validates payload at boundary (Phase 0 integration).

        Args:
            msg_type: Telemetry message type (e.g., "telemetry.resource.updated")
            data: Telemetry payload (will be validated)

        Returns:
            True if enqueued, False if dropped
        """
        from universal_protocol.messages import parse_telemetry

        # Validate at boundary (Phase 0 type safety)
        try:
            payload = parse_telemetry(msg_type, data)
            validated_data = payload.to_dict()
        except (ValueError, KeyError) as e:
            logger.error(
                f"Invalid telemetry payload: {msg_type} - {e}",
                extra={"msg_type": msg_type, "error": str(e)},
            )
            return False

        # Source rewriting handled at envelope creation
        # gateway_id preserved, stargate_id is this Edge's ID
        msg = MessageEnvelope(type=msg_type, data=validated_data)
        return await self.send(msg)

    async def _consumer_worker(self) -> None:
        """
        Consumer worker: Wait for upstream ready, then drain queue.

        Lifecycle:
        1. Wait for upstream ready
        2. Drain queue (send all buffered telemetry)
        3. Continue sending new telemetry
        4. If upstream disconnects, wait again (queue cleared by signal_disconnected)
        """
        logger.info("Consumer worker started (waiting for upstream)")

        while self._running:
            # Wait for upstream ready
            await self._ready.wait()

            if not self._running:
                break

            # Log buffered count only if non-zero
            buffered = self._queue.qsize()
            if buffered > 0:
                logger.info(
                    f"Upstream ready - draining {buffered} buffered telemetry items"
                )
            else:
                logger.info("Upstream ready - telemetry forwarding active")

            # Drain queue while upstream ready
            while self._ready.is_set() and self._running:
                try:
                    # Wait for telemetry with timeout (check readiness periodically)
                    msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)

                    # Send to upstream
                    success = await self._send_callback(msg)

                    if success:
                        self._sent_count += 1
                        # Throttled debug logging (every 50th message)
                        if self._sent_count % 50 == 1:
                            logger.debug(
                                f"Telemetry sent: {msg.type} (total={self._sent_count})"
                            )
                    else:
                        self._dropped_count += 1
                        logger.warning(f"Failed to send telemetry: {msg.type}")

                except TimeoutError:
                    # No telemetry in queue, check readiness again
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Consumer worker error: {e}", exc_info=True)

            if not self._ready.is_set():
                logger.info("Upstream disconnected - consumer paused")

    def _clear_queue(self) -> int:
        """Clear all queued telemetry."""
        cleared = 0
        while True:
            try:
                self._queue.get_nowait()
                cleared += 1
            except asyncio.QueueEmpty:
                break
        return cleared
