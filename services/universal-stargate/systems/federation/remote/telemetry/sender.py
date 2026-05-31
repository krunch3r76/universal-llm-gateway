"""
Telemetry sender for Remote mode.

Forwards local Gateway telemetry to Master via WebSocket.

INVARIANT: ∀ telemetry event: enhanced with TelemetrySource before send
INVARIANT: Rate limiter prevents overwhelming Master
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_event_bus.backpressure import (
    OverflowPolicy as BackpressureOverflowPolicy,
)
from universal_event_bus.backpressure import (
    RateLimitConfig,
    RateLimitedEventSource,
)
from universal_logging import get_logger
from universal_protocol.messages import (
    MessageEnvelope,
    ModelBusy,
    ModelIdle,
    ModelLoaded,
    ModelLoadFailed,
    ModelLoadingStarted,
    ModelUnloaded,
    ResourceUpdate,
    ResourceUpdatePayload,
    TelemetrySource,
)

from ...common.config import FederationConfig

logger = get_logger(__name__)


class RemoteTelemetrySender:
    """
    Sends telemetry from local Gateway/Edge to connected Master.

    Architecture:
        - Gateway telemetry: Uses rate limiter (N Gateway events → Master)
        - Edge telemetry: Uses rate limiter (Relay→Master is aggregation point)

    Rate limiting is applied at the Relay→Master boundary where multiple
    Relays (N:1) could flood Master. Edge topology behind each Relay is
    irrelevant to Master - all Relay→Master traffic is rate-limited.

    Lifecycle:
        1. Create with config and send_callback
        2. Call start() to initialize rate limiter
        3. Use on_* methods to forward telemetry
        4. Call stop() on shutdown
    """

    def __init__(
        self,
        config: FederationConfig,
        send_callback: Callable[[MessageEnvelope], Awaitable[bool]],
    ):
        """
        Initialize telemetry sender.

        Args:
            config: Federation configuration
            send_callback: Callback(message) → enqueued
        """
        self._config = config
        self._send_callback = send_callback

        # Build source identifier
        # In Relay topology, local_edge points to Edge Stargate
        # Use Edge's stargate_id as gateway_id for telemetry
        if config.local_edge:
            gateway_id = config.local_edge.stargate_id
        else:
            gateway_id = f"{config.stargate_id}/gateway"
        self._source = TelemetrySource(
            stargate_id=config.stargate_id,
            gateway_id=gateway_id,
            node_id=config.node_id,
        )

        # Rate limiter (optional, can be bypassed for low-volume)
        self._rate_limiter: RateLimitedEventSource | None = None
        self._running = False

        # Readiness gating (prevents pre-auth drops)
        self._master_ready = asyncio.Event()

        # Queue events when Master not ready (proper event replay)
        self._pending_events: asyncio.Queue[MessageEnvelope] = asyncio.Queue()

    def start(self) -> None:
        """Start the sender."""
        self._running = True

        # Convert config OverflowPolicy to backpressure module's OverflowPolicy
        overflow_policy = BackpressureOverflowPolicy(
            self._config.telemetry_backpressure.overflow_policy.value
        )

        config = RateLimitConfig(
            max_queue_size=self._config.telemetry_backpressure.max_queue_per_remote,
            max_events_per_second=self._config.telemetry_backpressure.max_events_per_second,
            overflow_policy=overflow_policy,
        )

        self._rate_limiter = RateLimitedEventSource(
            source_id="master",
            config=config,
            on_event=self._on_rate_limited_event,
            on_drop=self._on_drop,
        )
        self._rate_limiter.start()

        logger.info("RemoteTelemetrySender started")

    async def stop(self) -> None:
        """Stop the sender."""
        self._running = False

        if self._rate_limiter:
            await self._rate_limiter.stop()
            self._rate_limiter = None

        logger.info("RemoteTelemetrySender stopped")

    async def send_message(self, msg: MessageEnvelope) -> None:
        """
        Send telemetry message to Master (via rate limiter).

        Message already includes source in data field.

        INVARIANT: Readiness check BEFORE enqueueing (no dropping in callback).
        """
        if not self._running or not self._rate_limiter:
            logger.warning(
                f"Cannot send telemetry: running={self._running}, "
                f"rate_limiter={self._rate_limiter is not None}"
            )
            return

        # CRITICAL: Check readiness BEFORE enqueueing (proper gating)
        if not self._master_ready.is_set():
            # Queue event for replay when Master becomes ready
            await self._pending_events.put(msg)
            logger.debug(
                f"Queued {msg.type} for replay when Master ready "
                f"(pending_count={self._pending_events.qsize()})"
            )
            return

        # Throttled debug logging (log first + every 100th message)
        queue_depth = self._rate_limiter.queue_depth
        if queue_depth == 0 or queue_depth % 100 == 1:
            logger.debug(
                f"Enqueueing telemetry: {msg.type}, "
                f"queue_depth={queue_depth}, "
                f"source={self._source.stargate_id}/{self._source.gateway_id}"
            )

        # Store message in rate limiter queue (will be sent at controlled rate)
        await self._rate_limiter.enqueue(msg.type, msg)

    async def _on_rate_limited_event(
        self,
        msg_type: str,
        message: MessageEnvelope,
    ) -> None:
        """
        Called by rate limiter when event should be sent.

        INVARIANT: Readiness already checked before enqueueing.
        Rate limiter only throttles, doesn't gate on external conditions.
        """
        logger.debug(f"Rate limiter released event: {msg_type}")
        result = await self._send_callback(message)
        logger.debug(f"Send callback result: {result}")

    async def _on_drop(self, source_id: str, reason: str) -> None:
        """Handle dropped telemetry (backpressure)."""
        logger.debug(f"Telemetry dropped: {reason}")

    def signal_master_ready(self) -> None:
        """Signal Master connection ready and replay pending events."""
        self._master_ready.set()
        logger.info("✅ Master ready - telemetry forwarding active")

        # Replay all pending events
        pending_count = self._pending_events.qsize()
        if pending_count > 0:
            logger.info(f"📤 Replaying {pending_count} pending events to Master")
            asyncio.create_task(
                self._replay_pending_events(), name="replay-pending-telemetry"
            )
        else:
            logger.debug("No pending events to replay")

    async def _replay_pending_events(self) -> None:
        """Replay all pending events from queue."""
        while not self._pending_events.empty():
            try:
                event = self._pending_events.get_nowait()
                await self._send_callback(event)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f"Failed to replay event: {e}", exc_info=True)

    def signal_master_disconnected(self) -> None:
        """
        Signal Master disconnected.

        Clears readiness flag (stops rate limiter sends) then drains queue.
        Prevents unbounded memory growth and stale telemetry on reconnect.
        """
        # Step 1: Clear readiness (rate limiter callback stops sending)
        self._master_ready.clear()

        # Step 2: Drain rate limiter queue immediately
        if self._rate_limiter:
            cleared_count = self._rate_limiter.clear_queue()
            if cleared_count > 0:
                logger.warning(
                    f"⚠️ Master disconnected - "
                    f"cleared {cleared_count} queued telemetry items"
                )
            else:
                logger.info("⚠️ Master disconnected - no queued telemetry")
        else:
            logger.warning("⚠️ Master disconnected - telemetry paused")

    @property
    def is_master_ready(self) -> bool:
        """Check if Master is ready for telemetry."""
        return self._master_ready.is_set()

    async def _send_to_master(self, msg: MessageEnvelope) -> bool:
        """
        Send message directly to Master (bypasses rate limiter).

        Returns:
            True if sent, False if Master not ready
        """
        if not self._master_ready.is_set():
            return False

        return await self._send_callback(msg)

    async def forward_edge_telemetry(
        self,
        peer_id: str,
        msg_type: str,
        data: dict[str, Any],
    ) -> None:
        """
        Forward Edge telemetry to Master (for Relay Stargates).

        Uses rate limiter (Relay→Master is aggregation point, Master sees N Relays).
        Edge topology (1:1 vs N:1) is irrelevant to Master.

        Validates payload at boundary (Phase 0 integration).

        Args:
            peer_id: Edge stargate_id
            msg_type: Message type (e.g., "telemetry.resource.updated")
            data: Telemetry payload (will be validated)
        """
        from universal_protocol.messages import parse_telemetry

        # Validate at boundary (Phase 0 type safety)
        try:
            payload = parse_telemetry(msg_type, data)
        except (ValueError, KeyError) as e:
            logger.error(
                f"Invalid Edge telemetry payload: {msg_type} from {peer_id} - {e}",
                extra={"msg_type": msg_type, "peer_id": peer_id, "error": str(e)},
            )
            return

        # Rewrite source.stargate_id to Relay's identity
        # source may be None for request-scoped telemetry (e.g.
        # request.inference.started)
        # that originates from the edge Stargate event bus rather than the Gateway.
        if payload.source is not None:
            payload.source.stargate_id = self._source.stargate_id

        # Create envelope and enqueue (use rate limiter)
        msg = MessageEnvelope(type=msg_type, data=payload.to_dict())
        await self.send_message(msg)

        logger.debug(f"Enqueued Edge telemetry: {msg_type} from {peer_id}")

    # Gateway event handlers

    async def on_resource_update(self, raw: dict[str, Any]) -> None:
        """Handle resource update from local Gateway."""
        # Parse and validate at boundary
        payload = ResourceUpdatePayload.from_dict(raw)

        # Create new payload with our source
        # NOTE: We do NOT send available_models/model_resources here (static data).
        # Master preserves them from initial telemetry.
        msg_payload = ResourceUpdate(
            available_vram_mb=payload.available_vram_mb,
            available_ram_mb=payload.available_ram_mb,
            total_vram_mb=payload.total_vram_mb,
            total_ram_mb=payload.total_ram_mb,
            loaded_models=payload.loaded_models,
            busy_models=payload.busy_models,
            model_vram=payload.model_vram,
            # available_models/model_resources omitted
            # (static, sent only in initial telemetry)
            source=self._source,
        )

        msg = MessageEnvelope(
            type="telemetry.resource.updated",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)

    async def on_model_loaded(self, raw: dict[str, Any]) -> None:
        """Handle model loaded from local Gateway."""
        msg_payload = ModelLoaded(
            model_id=raw["model_id"],
            source=self._source,
            extra_data={k: v for k, v in raw.items() if k != "model_id"},
        )
        msg = MessageEnvelope(
            type="telemetry.model.loaded",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)

    async def on_model_unloaded(self, raw: dict[str, Any]) -> None:
        """Handle model unloaded from local Gateway."""
        msg_payload = ModelUnloaded(
            model_id=raw["model_id"],
            source=self._source,
        )
        msg = MessageEnvelope(
            type="telemetry.model.unloaded",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)

    async def on_model_busy(self, raw: dict[str, Any]) -> None:
        """Handle model busy from local Gateway."""
        msg_payload = ModelBusy(
            model_id=raw["model_id"],
            source=self._source,
        )
        msg = MessageEnvelope(
            type="telemetry.model.busy",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)

    async def on_model_idle(self, raw: dict[str, Any]) -> None:
        """Handle model idle from local Gateway."""
        msg_payload = ModelIdle(
            model_id=raw["model_id"],
            source=self._source,
        )
        msg = MessageEnvelope(
            type="telemetry.model.idle",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)

    async def on_model_loading_started(self, model_id: str) -> None:
        """Handle model loading started from local Gateway."""
        msg_payload = ModelLoadingStarted(
            model_id=model_id,
            source=self._source,
        )
        msg = MessageEnvelope(
            type="telemetry.model.loading.started",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)

    async def on_model_load_failed(
        self,
        model_id: str,
        error: str,
        worker_snapshot: dict | None = None,
        gateway_state_snapshot: dict | None = None,
    ) -> None:
        """Handle model load failed from local Gateway."""
        msg_payload = ModelLoadFailed(
            model_id=model_id,
            error=error,
            worker_snapshot=worker_snapshot,
            gateway_state_snapshot=gateway_state_snapshot,
            source=self._source,
        )
        msg = MessageEnvelope(
            type="telemetry.model.loading.failed",
            data=msg_payload.to_dict(),
        )
        await self.send_message(msg)
