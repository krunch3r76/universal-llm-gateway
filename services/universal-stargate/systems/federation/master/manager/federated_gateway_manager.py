"""
FederatedGatewayManager - event-driven state management for federated gateways.

INVARIANT: ∀ state_key, ∃! update_path (single source of truth via events)
INVARIANT: ∀ telemetry_receipt ⟹ timestamp_updated (freshness tracking)
INVARIANT: ∀ catalog_change ⟹ FEDERATION_GATEWAY_CATALOG_CHANGED event published

CRITICAL: Uses @sequential decorator for lock-free sequential execution.
         Must call start() before use and stop() on shutdown.
"""

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_event_bus import Sequential, sequential
from universal_logging import get_logger
from universal_protocol.messages import TelemetrySource

from ...common.protocol import (
    FederationMessageType,
)
from ...common.types import (
    FederatedGateway,
    extract_resource_state,
    parse_telemetry_payload,
)

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from systems.routing.capacity.ledger import CapacityLedger

logger = get_logger(__name__)


class FederatedGatewayManager(Sequential):
    """
    Manages federated gateway state via telemetry events.

    Uses Sequential base class with @sequential decorator for lock-free operation.

    INVARIANT:
      ∀ state update: via update_from_event() only
      ∧ ¬uses(asyncio.Lock)
      ∧ @sequential ensures no interleaving
      ∧ snapshot consistency for routing
      ∧ catalog_change ⟹ FEDERATION_GATEWAY_CATALOG_CHANGED event published

    Lifecycle:
      1. Create manager
      2. Call await manager.start() to start executor
      3. Use manager.update_from_event() for state updates
      4. Call await manager.stop() on shutdown
    """

    def __init__(
        self,
        event_bus: "EventBus",
        capacity_ledger: "CapacityLedger | None" = None,
    ):
        super().__init__()
        self._event_bus = event_bus
        self._capacity_ledger = capacity_ledger

        # Gateway state by gateway_id
        self._gateways: dict[str, FederatedGateway] = {}

        # Remote URLs by remote_stargate_id
        self._remote_urls: dict[str, str] = {}

        # HTTP polling remotes (for setting is_http_polling flag)
        self._http_polling_remotes: set[str] = set()

        # Remote configs by remote_stargate_id
        self._remote_configs: dict[str, Any] = {}  # RemoteStargateConfig

        # Load failure tracking: gateway_id → {routing_key, ...}
        # INV: routing_key ∈ _load_failed_models[gw] ⟹ model ineligible on gw
        # INV: cleared on gateway disconnect/reconnect or telemetry MODEL_LOADED
        # TODO: blacklist is permanent until gateway reconnects. For transient
        # failures (e.g. OOM due to marginal VRAM when a non-CUDA consumer
        # releases memory between check and load), this permanently blocks retry
        # on the only eligible gateway. Implement a time-based TTL (e.g. 5 min)
        # or a retry budget (e.g. 3 attempts) keyed by (gateway_id, routing_key)
        # so that transient OOMs are retried while persistent failures are not.
        self._load_failed_models: dict[str, set[str]] = {}

    async def start(self) -> None:
        """
        Start the sequential executor.

        MUST be called before update_from_event() to enable sequential guarantees.
        """

        await self._start_executor()
        logger.info("FederatedGatewayManager started")

    async def stop(self) -> None:
        """Stop the sequential executor."""
        await self._stop_executor()
        logger.info("FederatedGatewayManager stopped")

    def set_capacity_ledger(self, capacity_ledger: "CapacityLedger") -> None:
        """
        Wire capacity ledger for admission control.

        Args:
            capacity_ledger: CapacityLedger instance for tracking concurrency slots
        """
        self._capacity_ledger = capacity_ledger
        seeded_slots = 0
        seeded_gateways = 0

        # Backfill: wiring may happen after initial GATEWAY_SNAPSHOT due to
        # router-only Master startup ordering. Seed from currently known gateways.
        if self._capacity_ledger:
            for gw in self._gateways.values():
                if not gw.model_resources:
                    continue
                seeded_gateways += 1
                for model_id, res in gw.model_resources.items():
                    max_concurrent_raw = res.get("max_concurrent_requests", 1)
                    try:
                        max_concurrent = int(max_concurrent_raw)
                    except (TypeError, ValueError):
                        logger.error(
                            "Invalid max_concurrent_requests=%r for %s/%s; "
                            "defaulting to 1",
                            max_concurrent_raw,
                            gw.gateway_id,
                            model_id.routing_key,
                        )
                        max_concurrent = 1

                    self._capacity_ledger.set_capacity(
                        gateway_id=gw.gateway_id,
                        model_id=model_id.routing_key,
                        max_concurrent=max_concurrent,
                    )
                    seeded_slots += 1

        logger.info(
            "✅ Capacity ledger wired to FederatedGatewayManager "
            "(seeded %d model slots across %d gateways)",
            seeded_slots,
            seeded_gateways,
        )

    def register_remote(
        self,
        remote_stargate_id: str,
        url: str,
        is_http_polling: bool = False,
        config: Any = None,
    ) -> None:
        """
        Register a Remote Stargate URL for gateway creation.

        Args:
            remote_stargate_id: Remote Stargate identifier
            url: Remote Stargate URL
            is_http_polling: If True, mark this remote as HTTP-polling (no WebSocket)
            config: Optional RemoteStargateConfig

        Note: Gateway creation happens in update_from_event() (under @sequential).
              This method only stores configuration.
        """
        self._remote_urls[remote_stargate_id] = url
        if is_http_polling:
            self._http_polling_remotes.add(remote_stargate_id)
        if config is not None:
            self._remote_configs[remote_stargate_id] = config

    def _coerce_gateway_if_stale(
        self, gateway_id: str, gateway: Any
    ) -> FederatedGateway:
        """
        Detect and upgrade stale FederatedGateway instances.

        Stale instances occur when:
        - Hot reload changes FederatedGateway class definition
        - Partial deploy mixes old/new class versions

        Detection: isinstance check fails OR hasattr for new fields fails

        INVARIANT: returned gateway is current FederatedGateway class

        Args:
            gateway_id: Gateway identifier for logging
            gateway: Potentially stale gateway object

        Returns:
            Current FederatedGateway instance (original or reconstructed)
        """
        # Fast path: already correct type
        if isinstance(gateway, FederatedGateway):
            return gateway

        # Stale instance detected
        logger.error(
            f"🔄 STALE GATEWAY DETECTED: {gateway_id} - "
            f"type={type(gateway).__name__}. "
            f"Reconstructing from current class definition. "
            f"This indicates hot reload or mixed deploy."
        )

        # Extract identity and state from stale instance
        remote_stargate_id = getattr(gateway, "remote_stargate_id", "unknown")

        # Reconstruct with current class
        new_gateway = FederatedGateway(
            gateway_id=gateway_id,
            remote_stargate_id=remote_stargate_id,
            remote_stargate_url=getattr(gateway, "remote_stargate_url", ""),
            node_id=getattr(gateway, "node_id", ""),
            is_http_polling=getattr(gateway, "is_http_polling", False),
            # Preserve resource state
            ram_free_mb=getattr(gateway, "ram_free_mb", 0),
            vram_free_mb=getattr(gateway, "vram_free_mb", 0),
            ram_total_mb=getattr(gateway, "ram_total_mb", 0),
            vram_total_mb=getattr(gateway, "vram_total_mb", 0),
            # Preserve model state
            loaded_models=getattr(gateway, "loaded_models", frozenset()),
            busy_models=getattr(gateway, "busy_models", frozenset()),
            loading_models=getattr(gateway, "loading_models", frozenset()),
            available_models=getattr(gateway, "available_models", frozenset()),
            # None = not provided (fallback to available_models)
            activated_models=getattr(gateway, "activated_models", None),
            model_resources=getattr(gateway, "model_resources", {}),
            activated_contexts=getattr(gateway, "activated_contexts", {}),
            # Preserve request state
            active_requests=getattr(gateway, "active_requests", 0),
            # Preserve timestamps
            telemetry_timestamp=getattr(gateway, "telemetry_timestamp", 0.0),
            last_heartbeat=getattr(gateway, "last_heartbeat", 0.0),
        )

        # Replace in registry
        self._gateways[gateway_id] = new_gateway

        return new_gateway

    def get_gateway(self, gateway_id: str) -> FederatedGateway | None:
        """Get gateway by ID."""
        return self._gateways.get(gateway_id)

    def get_all_gateways(self) -> list[FederatedGateway]:
        """Get all federated gateways (for routing)."""
        return list(self._gateways.values())

    def get_healthy_gateways(self) -> list[FederatedGateway]:
        """Get gateways that are not unreachable (no signal within TTL)."""

        healthy = []
        unreachable = []

        for gateway_id, gw in list(self._gateways.items()):
            # Coerce stale instances before evaluation
            gw = self._coerce_gateway_if_stale(gateway_id, gw)

            if gw.is_unreachable:
                age_ms = gw.heartbeat_age_ms
                unreachable.append((gw.gateway_id, age_ms))
            else:
                healthy.append(gw)

        # Log unreachable gateways as warnings
        for gateway_id, age_ms in unreachable:
            logger.warning(
                f"⚠️ Gateway {gateway_id} is UNREACHABLE "
                f"(no signal for {age_ms}ms, threshold=60000ms)"
            )

        logger.info(
            f"🔍 get_healthy_gateways() returning {len(healthy)} gateways "
            f"(total: {len(self._gateways)}, unreachable: {len(unreachable)})"
        )
        for gw in healthy:
            logger.info(
                f"  → {gw.gateway_id}: available_models={len(gw.available_models)}, "
                f"sample={list(gw.available_models)[:2]}"
            )
        return healthy

    # === Query Methods for Orchestration ===

    def get_gateway_status_full(self) -> dict[str, dict[str, Any]]:
        """
        Get full status of all federated gateways including VRAM/RAM capacity
        and models.

        Returns:
            Dict mapping gateway_id to full status dict with:
            - enabled: bool (always True for federated gateways)
            - is_connected: bool (True if not unreachable)
            - total_vram_mb: int
            - available_vram_mb: int
            - total_ram_mb: int
            - available_ram_mb: int
            - models: list[str] (available models on this gateway)
        """
        result = {}
        for gw in self._gateways.values():
            result[gw.gateway_id] = {
                "enabled": True,
                "is_connected": not gw.is_unreachable,
                "total_vram_mb": gw.vram_total_mb,
                "available_vram_mb": gw.vram_free_mb,
                "total_ram_mb": gw.ram_total_mb,
                "available_ram_mb": gw.ram_free_mb,
                "models": [str(model_id) for model_id in gw.available_models],
            }
        return result

    def is_model_believed_loaded(self, gateway_id: str, model_id: ModelId) -> bool:
        """
        Check if telemetry HINTS that model is loaded.

        WARNING: This is a HINT, not authoritative. Caller MUST handle
        the case where this returns True but model is actually not loaded
        (split-brain scenario).

        Args:
            gateway_id: Gateway identifier (primitive for clean interface)
            model_id: Model to check (ModelId object, NOT string)

        Returns:
            True if telemetry indicates model is loaded, False otherwise
        """
        gateway = self._gateways.get(gateway_id)
        if not gateway:
            logger.info(
                f"🔍 [TELEMETRY] Model belief check: gateway={gateway_id} NOT FOUND, "
                f"model={model_id}, believed_loaded=false"
            )
            return False

        # ModelId comparison handles normalization (see ModelId.__eq__)
        result = model_id in gateway.loaded_models

        # DIAGNOSTIC LOGGING (permanent, not temporary)
        logger.info(
            f"🔍 [TELEMETRY] Model belief check: gateway={gateway_id}, "
            f"model={model_id}, believed_loaded={result}, "
            f"loaded_models_count={len(gateway.loaded_models)}"
        )
        if not result and len(gateway.loaded_models) > 0:
            # Show sample of what IS loaded (helps diagnosis)
            sample = [str(m) for m in list(gateway.loaded_models)[:5]]
            logger.debug(f"🔍 [TELEMETRY] Loaded models sample: {sample}")

        return result

    def get_telemetry_age_seconds(self, gateway_id: str) -> float | None:
        """
        Get seconds since last telemetry update.

        Args:
            gateway_id: Gateway identifier (primitive for clean interface)

        Returns:
            Age in seconds, or None if gateway unknown or no telemetry received
        """
        gateway = self._gateways.get(gateway_id)
        if not gateway:
            return None
        return time.time() - gateway.telemetry_timestamp

    @sequential
    async def is_telemetry_fresh(
        self, gateway_id: str, threshold_seconds: float
    ) -> bool:
        """
        Check if telemetry is within freshness threshold.

        Args:
            gateway_id: Gateway identifier (primitive for clean interface)
            threshold_seconds: Maximum acceptable age in seconds

        Returns:
            True if telemetry age <= threshold, False if stale or unknown

        Note:
            @sequential required for safe _gateways dict access (line 321)
        """
        age = self.get_telemetry_age_seconds(gateway_id)

        # Handle no telemetry case
        if age is None:
            logger.warning(
                f"🔍 [TELEMETRY] No heartbeat recorded for {gateway_id} - "
                f"treating as stale"
            )
            return False

        is_fresh = age <= threshold_seconds

        # DIAGNOSTIC LOGGING (permanent)
        logger.info(
            f"🔍 [TELEMETRY] Freshness check: gateway={gateway_id}, "
            f"age={age:.1f}s, threshold={threshold_seconds}s, fresh={is_fresh}"
        )

        # Emit staleness event if telemetry is stale
        if not is_fresh:
            import asyncio

            from src.scheduling.events import FederationTelemetryMarkedStale

            # Get remote_id from gateway (guaranteed to exist since we have age)
            gw = self._gateways.get(gateway_id)
            if gw:
                remote_id = gw.remote_stargate_id
                asyncio.create_task(
                    self._event_bus.publish_async_nowait(
                        FederationTelemetryMarkedStale(
                            remote_id=remote_id,
                            age_seconds=age,
                            threshold_seconds=threshold_seconds,
                        )
                    )
                )

        return is_fresh

    def mark_loading_optimistic(self, gateway_id: str, model_id: ModelId) -> bool:
        """
        Synchronously mark model as loading for immediate visibility.

        CRITICAL: This is NOT @sequential - it updates state immediately so
        concurrent select() calls see the loading mark without awaiting.

        Thread-safety: Single-threaded asyncio event loop - no race between
        sync operations within same coroutine execution.

        Args:
            gateway_id: Gateway identifier
            model_id: Model being loaded

        Returns:
            True if marked, False if gateway not found
        """
        gw = self._gateways.get(gateway_id)
        if not gw:
            logger.warning(
                f"Cannot mark loading (optimistic): gateway {gateway_id} not found "
                f"(model: {model_id})"
            )
            return False

        # Immediate frozenset update - no await, no queue
        gw.loading_models = gw.loading_models | {model_id}
        logger.info(
            f"🔄 OPTIMISTIC MARK: {model_id} → {gateway_id} "
            f"(loading_count={len(gw.loading_models)})"
        )
        return True

    @sequential
    async def mark_model_loading(self, gateway_id: str, model_id: ModelId) -> None:
        """
        Optimistically mark model as loading before RPC call.

        This enables cold-load spreading for simultaneous requests:
        - Request 1 selects gateway A, marks model as loading, sends RPC
        - Request 2 sees gateway A has loading model, selects gateway B instead
        - Telemetry later confirms/reconciles actual state

        INVARIANT: Called BEFORE load RPC to prevent race with telemetry
        INVARIANT: Cleared by telemetry (MODEL_LOADED/MODEL_LOAD_FAILED) or timeout

        Args:
            gateway_id: Gateway identifier
            model_id: Model being loaded
        """
        gw = self._gateways.get(gateway_id)
        if not gw:
            logger.warning(
                f"Cannot mark loading: gateway {gateway_id} not found "
                f"(model: {model_id})"
            )
            return

        # Add to loading set (frozenset requires replacement)
        gw.loading_models = gw.loading_models | {model_id}
        logger.debug(
            f"🔄 Marked {model_id} as loading on {gateway_id} "
            f"(loading_count={len(gw.loading_models)})"
        )

    @sequential
    async def clear_model_loading(self, gateway_id: str, model_id: ModelId) -> None:
        """
        Clear loading state (called on load failure or timeout).

        Telemetry normally clears this via MODEL_LOADED/MODEL_UNLOADED,
        but this method handles failure cases where telemetry won't arrive.

        Args:
            gateway_id: Gateway identifier
            model_id: Model to clear from loading set
        """
        gw = self._gateways.get(gateway_id)
        if not gw:
            return

        if model_id in gw.loading_models:
            gw.loading_models = gw.loading_models - {model_id}
            logger.debug(
                f"🔄 Cleared loading state for {model_id} on {gateway_id} "
                f"(loading_count={len(gw.loading_models)})"
            )

    # === Load Failure Tracking ===

    def mark_load_failed(self, gateway_id: str, model_id: ModelId) -> None:
        """Record that a model failed to load on a gateway.

        The model becomes ineligible for routing on this gateway until
        the gateway disconnects/reconnects or telemetry confirms it loaded.
        """
        routing_key = model_id.routing_key
        failed = self._load_failed_models.setdefault(gateway_id, set())
        failed.add(routing_key)
        logger.warning(
            "🚫 Load failure recorded: %s on %s (%d total failures)",
            model_id,
            gateway_id,
            len(failed),
        )

    def is_load_failed(self, gateway_id: str, model_id: ModelId) -> bool:
        """Check whether a model previously failed to load on a gateway."""
        return model_id.routing_key in self._load_failed_models.get(gateway_id, set())

    def clear_load_failures(self, gateway_id: str) -> None:
        """Clear all load failures for a gateway (called on disconnect/reconnect)."""
        removed = self._load_failed_models.pop(gateway_id, None)
        if removed:
            logger.info(
                "🔓 Cleared %d load failure(s) for %s", len(removed), gateway_id
            )

    def _clear_model_load_failure(self, gateway_id: str, model_id: ModelId) -> None:
        """Clear a single model's load failure (called when telemetry confirms load)."""
        failed = self._load_failed_models.get(gateway_id)
        if failed and model_id.routing_key in failed:
            failed.discard(model_id.routing_key)
            if not failed:
                del self._load_failed_models[gateway_id]
            logger.info(
                "🔓 Cleared load failure for %s on %s (telemetry confirmed loaded)",
                model_id,
                gateway_id,
            )

    # === Gateway Creation (shared by all ingestion paths) ===

    def _ensure_gateway(
        self,
        gateway_id: str,
        remote_stargate_id: str,
        node_id: str = "",
    ) -> FederatedGateway:
        """
        Get or create gateway for telemetry ingestion.

        INVARIANT: ∀ gateway_id, remote_stargate_id ⟹ gateway ∈ _gateways

        Args:
            gateway_id: Gateway identifier (from telemetry)
            remote_stargate_id: Remote Stargate ID (for URL lookup + mode)
            node_id: Canonical node identifier (from telemetry source)

        Returns:
            Existing or newly created FederatedGateway
        """
        if gateway_id in self._gateways:
            gw = self._gateways[gateway_id]
            if node_id and not gw.node_id:
                gw.node_id = node_id
            return gw

        # Look up remote configuration
        remote_url = self._remote_urls.get(remote_stargate_id, "")
        is_http_polling = remote_stargate_id in self._http_polling_remotes

        # Create gateway
        gateway = FederatedGateway(
            gateway_id=gateway_id,
            remote_stargate_id=remote_stargate_id,
            remote_stargate_url=remote_url,
            node_id=node_id,
            is_http_polling=is_http_polling,
        )
        self._gateways[gateway_id] = gateway

        mode = "HTTP-polling" if is_http_polling else "WebSocket"
        logger.info(
            f"🌐 New federated gateway registered: {gateway_id} "
            f"(node={node_id or 'unknown'}, {mode})"
        )

        return gateway

    def _update_telemetry_timestamps(self, gateway: FederatedGateway) -> None:
        """
        Update telemetry freshness timestamps.

        INVARIANT: ∀ telemetry_receipt ⟹ timestamps updated

        Must be called after every telemetry ingestion to prevent
        gateway from becoming "unreachable" (60s threshold).
        """
        now = time.time()
        old_heartbeat = gateway.last_heartbeat
        was_unreachable = gateway.is_unreachable

        gateway.telemetry_timestamp = now
        gateway.last_heartbeat = now

        # Log state change if transitioning from unreachable to reachable
        if was_unreachable:
            age_before = int((now - old_heartbeat) * 1000)
            logger.warning(
                f"🔄 Gateway {gateway.gateway_id} transition: UNREACHABLE → REACHABLE "
                f"(was offline for {age_before}ms)"
            )

    def _update_heartbeat_timestamp(self, gateway: FederatedGateway) -> None:
        """Update heartbeat timestamp (liveness signal only)."""
        now = time.time()
        old_heartbeat = gateway.last_heartbeat
        was_unreachable = gateway.is_unreachable

        gateway.last_heartbeat = now

        # Log state change if transitioning from unreachable to reachable
        if was_unreachable:
            age_before = int((now - old_heartbeat) * 1000)
            logger.warning(
                f"💓 Gateway {gateway.gateway_id} heartbeat restored: "
                f"UNREACHABLE → REACHABLE (was offline for {age_before}ms)"
            )
        else:
            gap_ms = int((now - old_heartbeat) * 1000)
            if gap_ms > 15_000:
                logger.warning(
                    f"⚠️ Gateway {gateway.gateway_id} heartbeat gap: "
                    f"{gap_ms}ms (expected ≤5000ms)"
                )

    @sequential
    async def update_from_event(
        self,
        remote_id: str,
        msg_type: str,
        data: dict[str, Any],
    ) -> None:
        """
        Update gateway state from telemetry event.

        INVARIANT: This is the ONLY path for state updates
        INVARIANT: Telemetry is HINT only; HTTP is authoritative

        NOTE: @sequential decorator ensures no concurrent execution.
              Executor must be started via start() before calling.
        """

        logger.debug(f"🔄 Telemetry event: remote_id={remote_id}, type={msg_type}")

        # Parse message data (ModelId conversion at boundary)
        parsed = parse_telemetry_payload(msg_type, data)

        # Extract and validate source
        source_data = parsed.get("source", {})
        if not source_data:
            logger.warning(f"📊 Telemetry missing source: {msg_type}")
            return

        try:
            source = TelemetrySource.from_dict(source_data)
        except KeyError as e:
            logger.warning(f"📊 Invalid telemetry source: {e}")
            return

        # Validate remote_id matches source (security: prevent spoofing)
        if source.stargate_id != remote_id:
            logger.warning(
                f"📊 Stargate ID mismatch: expected {remote_id}, "
                f"got {source.stargate_id}"
            )
            return

        gateway_id = source.gateway_id

        # Get or create gateway (shared helper)
        gw = self._ensure_gateway(gateway_id, source.stargate_id, source.node_id)

        # Pre-condition observation (for observability)
        pre_loaded_models = gw.loaded_models

        # Update based on message type
        if msg_type == FederationMessageType.GATEWAY_SNAPSHOT.value:
            self._apply_gateway_snapshot(gw, parsed)
        elif msg_type == FederationMessageType.RESOURCE_UPDATE.value:
            self._apply_resource_update(gw, parsed)
        elif msg_type == FederationMessageType.MODEL_LOADING_STARTED.value:
            self._apply_model_loading_started(gw, parsed)
        elif msg_type == FederationMessageType.MODEL_LOADED.value:
            self._apply_model_loaded_with_logging(gw, parsed, pre_loaded_models)
        elif msg_type == FederationMessageType.MODEL_LOAD_FAILED.value:
            self._apply_model_load_failed(gw, parsed)
        elif msg_type == FederationMessageType.MODEL_UNLOADED.value:
            self._apply_model_unloaded(gw, parsed)
        elif msg_type == FederationMessageType.MODEL_BUSY.value:
            self._apply_model_busy(gw, parsed)
        elif msg_type == FederationMessageType.MODEL_IDLE.value:
            self._apply_model_idle(gw, parsed)
        elif msg_type == FederationMessageType.TELEMETRY_HEARTBEAT.value:
            # Heartbeat: liveness only (must not refresh resource freshness)
            self._update_heartbeat_timestamp(gw)
            return

        # Update timestamps (CRITICAL: prevents gateway from becoming unreachable)
        self._update_telemetry_timestamps(gw)

        # Publish GATEWAY_RESOURCE_UPDATE for TelemetryFreshnessWaiter integration
        # This wakes waiting requests in Master mode sticky queue wait
        if self._event_bus:
            import asyncio

            from src.scheduling.events import GatewayResourceUpdate

            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    GatewayResourceUpdate(
                        url=gw.remote_stargate_url,
                        total_vram_mb=gw.vram_total_mb,
                        available_vram_mb=gw.vram_free_mb,
                        total_ram_mb=gw.ram_total_mb,
                        available_ram_mb=gw.ram_free_mb,
                        loaded_models=[str(m) for m in gw.loaded_models],
                        busy_models=[str(m) for m in gw.busy_models],
                    )
                )
            )
            logger.debug(
                f"📢 Published GATEWAY_RESOURCE_UPDATE for {gateway_id} "
                f"(waking TelemetryFreshnessWaiter)"
            )

    def _apply_gateway_snapshot(
        self, gw: FederatedGateway, parsed: dict[str, Any]
    ) -> None:
        """
        Apply GATEWAY_SNAPSHOT - initial catalog data + resource state.

        Sent once when Edge Stargate first wires Gateway telemetry.
        Contains complete catalog (available_models, model_resources) plus
        initial resource metrics.

        Unlike RESOURCE_UPDATE, this event ALWAYS includes catalog data
        and is the authoritative source for initial catalog.

        INVARIANT: gateway_snapshot contains complete catalog
        INVARIANT: catalog_change ⟹ FEDERATION_GATEWAY_CATALOG_CHANGED event
        """
        state = extract_resource_state(parsed)

        # Recommendation #3: Debug logging for telemetry pipeline tracing
        model_resources = state.get("model_resources", {})
        logger.info(
            f"📊 GATEWAY_SNAPSHOT from {gw.gateway_id}: "
            f"{len(state.get('available_models', []))} models, "
            f"available={state.get('available_vram_mb')}MB VRAM"
        )
        logger.debug(
            f"📊 [TELEMETRY] Master received GATEWAY_SNAPSHOT: "
            f"{len(model_resources)} model_resources entries"
        )
        if model_resources:
            sample = list(model_resources.items())[:3]
            logger.debug(f"📊 [TELEMETRY] Sample model_resources: {sample}")

        # Detect catalog changes (for pipeline reload trigger)
        old_catalog = gw.available_models
        new_catalog = state["available_models"]

        catalog_changed = old_catalog != new_catalog

        # Detailed catalog change logging
        if catalog_changed:
            added = new_catalog - old_catalog
            removed = old_catalog - new_catalog
            logger.info(
                f"📦 Master: Catalog initialized for {gw.gateway_id}: "
                f"old={len(old_catalog)}, new={len(new_catalog)}, "
                f"added={len(added)}, removed={len(removed)}"
            )
            if added:
                logger.debug(f"  ➕ Added: {list(added)[:10]}")
            if removed:
                logger.debug(f"  ➖ Removed: {list(removed)[:10]}")

        # Update ALL fields (catalog + resources + activation)
        gw.ram_free_mb = state["ram_free_mb"]
        gw.vram_free_mb = state["vram_free_mb"]
        gw.ram_total_mb = state["ram_total_mb"]
        gw.vram_total_mb = state["vram_total_mb"]
        gw.available_models = new_catalog
        # None = not provided, frozenset() = explicitly empty
        activated_models = state.get("activated_models")
        gw.activated_models = activated_models
        gw.activated_contexts = state.get("activated_contexts", {})
        gw.active_requests = state.get("active_requests", 0)
        gw.model_resources = state.get("model_resources", {})

        # Seed capacity ledger from model_resources (admission control)
        if self._capacity_ledger and gw.model_resources:
            for model_id, res in gw.model_resources.items():
                max_concurrent_raw = res.get("max_concurrent_requests", 1)
                try:
                    max_concurrent = int(max_concurrent_raw)
                except (TypeError, ValueError):
                    logger.error(
                        "Invalid max_concurrent_requests=%r for %s/%s; defaulting to 1",
                        max_concurrent_raw,
                        gw.gateway_id,
                        model_id.routing_key,
                    )
                    max_concurrent = 1
                self._capacity_ledger.set_capacity(
                    gateway_id=gw.gateway_id,
                    model_id=model_id.routing_key,
                    max_concurrent=max_concurrent,
                )
            logger.debug(
                f"📊 Capacity ledger updated from GATEWAY_SNAPSHOT: "
                f"{len(gw.model_resources)} models on {gw.gateway_id}"
            )

        # Debug logging for activation filtering verification
        activated_is_none = activated_models is None
        activated_count = 0 if activated_is_none else len(activated_models)
        logger.debug(
            f"📋 GATEWAY_SNAPSHOT activation: gateway={gw.gateway_id}, "
            f"available={len(new_catalog)}, activated={activated_count}, "
            f"activated_is_none={activated_is_none}"
        )

        # Publish catalog change event (for pipeline system to reload)
        if catalog_changed:
            logger.info(
                f"📦 Catalog changed for gateway {gw.gateway_id}: "
                f"{len(old_catalog)} → {len(new_catalog)} models"
            )
            import asyncio

            from src.scheduling.events import FederationGatewayCatalogChanged

            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    FederationGatewayCatalogChanged(
                        gateway_id=gw.gateway_id,
                        old_model_count=len(old_catalog),
                        new_model_count=len(new_catalog),
                    )
                )
            )

    def _apply_resource_update(
        self, gw: FederatedGateway, parsed: dict[str, Any]
    ) -> None:
        """
        Apply RESOURCE_UPDATE - resource metrics only.

        CRITICAL: Model lifecycle state (loaded_models, busy_models, loading_models)
        is ONLY updated by discrete events (MODEL_LOADED, MODEL_UNLOADED, etc).
        RESOURCE_UPDATE must NEVER overwrite these fields to prevent race conditions.

        NOTE: Catalog data (available_models, model_resources) should NOT be in
        RESOURCE_UPDATE - use GATEWAY_SNAPSHOT for initial catalog.

        Race condition scenario (FIXED):
        1. Remote starts loading model (takes 45s)
        2. Remote sends MODEL_LOADED when complete
        3. Master receives MODEL_LOADED, sets loaded_models = {model}
        4. Remote sends RESOURCE_UPDATE with snapshot from BEFORE load completed
        5. OLD CODE: Master overwrites loaded_models = {} (WRONG)
        6. NEW CODE: Master preserves loaded_models = {model} (CORRECT)

        INVARIANT: resource_update updates ONLY: ram/vram metrics, active_requests
        INVARIANT: ∀ model_lifecycle_state: updated ONLY by discrete events
        """
        state = extract_resource_state(parsed)

        # Validation logging for telemetry flow
        logger.info(
            f"📥 RESOURCE_UPDATE from {gw.gateway_id}: "
            f"available={state.get('vram_free_mb')}MB, "
            f"total={state.get('vram_total_mb')}MB"
        )

        # Update ONLY resource metrics (not model lifecycle state or catalog)
        gw.ram_free_mb = state["ram_free_mb"]
        gw.vram_free_mb = state["vram_free_mb"]
        gw.ram_total_mb = state["ram_total_mb"]
        gw.vram_total_mb = state["vram_total_mb"]
        gw.active_requests = state.get("active_requests", 0)

        logger.debug(
            f"📦 Master: Applied RESOURCE_UPDATE for {gw.gateway_id}: "
            f"vram={gw.vram_free_mb}MB, ram={gw.ram_free_mb}MB"
        )

    def _apply_model_loading_started(
        self, gw: FederatedGateway, parsed: dict[str, Any]
    ) -> None:
        """
        Apply MODEL_LOADING_STARTED event (telemetry reconciliation).

        This is for telemetry-driven updates. Optimistic local tracking happens
        via mark_model_loading() before RPC is sent.

        If telemetry arrives before our local mark (unlikely), this still works.
        """
        model_id = parsed.get("model_id")
        if not model_id:
            return
        gw.loading_models = gw.loading_models | {model_id}
        logger.debug(
            f"📊 Telemetry MODEL_LOADING_STARTED: {model_id} on {gw.gateway_id} "
            f"(loading_count={len(gw.loading_models)})"
        )

    def _apply_model_loaded_with_logging(
        self,
        gw: FederatedGateway,
        parsed: dict[str, Any],
        pre_loaded_models: frozenset[ModelId],
    ) -> None:
        """
        Apply MODEL_LOADED event with invariant logging.

        INVARIANT: Telemetry is HINT only. Does not affect orchestration correctness.
        """
        model_id = parsed.get("model_id")
        if not model_id:
            return

        # Pre-condition observation
        was_already_loaded = model_id in pre_loaded_models
        if was_already_loaded:
            logger.debug(
                f"📊 Telemetry MODEL_LOADED for {model_id} on {gw.gateway_id}: "
                "model was already in loaded_models (idempotent)"
            )

        # Apply update (idempotent set union)
        gw.loaded_models = gw.loaded_models | {model_id}
        gw.loading_models = gw.loading_models - {model_id}

        # Telemetry confirms model loaded — clear any prior load failure
        if isinstance(model_id, ModelId):
            self._clear_model_load_failure(gw.gateway_id, model_id)

        # Post-condition observation
        logger.debug(
            f"📊 Telemetry hint updated: {model_id} loaded on {gw.gateway_id} "
            f"(total: {len(gw.loaded_models)} loaded)"
        )

    def _apply_model_unloaded(
        self, gw: FederatedGateway, parsed: dict[str, Any]
    ) -> None:
        """
        Apply MODEL_UNLOADED event.

        Emits MODEL_UNLOADED to EventBus for unified eviction waiting
        (same event as local gateways, enables path-agnostic eviction).
        """
        model_id = parsed.get("model_id")
        if not model_id:
            return
        gw.loaded_models = gw.loaded_models - {model_id}
        gw.busy_models = gw.busy_models - {model_id}
        gw.loading_models = gw.loading_models - {model_id}

        # Remove capacity from ledger (admission control)
        if self._capacity_ledger:
            self._capacity_ledger.remove_model(gw.gateway_id, model_id.routing_key)
            logger.debug(
                f"📊 Capacity ledger: removed model {model_id} from {gw.gateway_id}"
            )

        # Emit MODEL_UNLOADED event to EventBus (unified with local path)
        if self._event_bus:
            import asyncio

            from src.scheduling.events import ModelUnloaded

            asyncio.create_task(
                self._event_bus.publish_async_nowait(
                    ModelUnloaded(
                        url=gw.remote_stargate_url,
                        model_id=model_id,
                        gateway_name=gw.gateway_id,
                    )
                )
            )
            logger.debug(
                f"📢 Published MODEL_UNLOADED event for {model_id} "
                f"on {gw.gateway_id} (unified eviction path)"
            )

    def _apply_model_load_failed(
        self, gw: FederatedGateway, parsed: dict[str, Any]
    ) -> None:
        """
        Apply MODEL_LOAD_FAILED event.

        Clears loading state since model won't be loaded.
        """
        model_id = parsed.get("model_id")
        if not model_id:
            return
        gw.loading_models = gw.loading_models - {model_id}
        logger.debug(
            f"📊 Telemetry MODEL_LOAD_FAILED: cleared loading for {model_id} "
            f"on {gw.gateway_id} (loading_count={len(gw.loading_models)})"
        )

    def _apply_model_busy(self, gw: FederatedGateway, parsed: dict[str, Any]) -> None:
        """Apply MODEL_BUSY event."""
        model_id = parsed.get("model_id")
        if not model_id:
            return
        gw.busy_models = gw.busy_models | {model_id}

    def _apply_model_idle(self, gw: FederatedGateway, parsed: dict[str, Any]) -> None:
        """Apply MODEL_IDLE event."""
        model_id = parsed.get("model_id")
        if not model_id:
            return
        gw.busy_models = gw.busy_models - {model_id}

    @sequential
    async def remove_remote_gateways(self, remote_stargate_id: str) -> list[str]:
        """
        Remove all gateways from a Remote Stargate (on disconnect).

        NOTE: @sequential ensures no concurrent execution with update_from_event().
        """
        removed = []
        for gateway_id in list(self._gateways.keys()):
            gw = self._gateways[gateway_id]
            if gw.remote_stargate_id == remote_stargate_id:
                # Remove capacity from ledger (admission control)
                if self._capacity_ledger:
                    self._capacity_ledger.remove_gateway(gateway_id)
                    logger.debug(f"📊 Capacity ledger: removed gateway {gateway_id}")
                self.clear_load_failures(gateway_id)
                del self._gateways[gateway_id]
                removed.append(gateway_id)

        if removed:
            logger.info(f"Removed {len(removed)} gateways from {remote_stargate_id}")
        return removed

    # === HTTP Polling Ingestion (apply_delta / apply_snapshot) ===

    @sequential
    async def apply_delta(
        self,
        gateway_id: str,
        delta: dict[str, Any],
        sequence_number: int,
        *,
        remote_stargate_id: str,
    ) -> None:
        """
        Apply received delta to gateway state (HTTP polling path).

        Master-side: Only applies, never computes deltas.
        Edge-first pattern: Remotes compute, Master applies.

        INVARIANT: ∀ delta_receipt ⟹ timestamps_updated (freshness)

        Args:
            gateway_id: Gateway to update
            delta: Changes to apply (pre-computed by Remote)
            sequence_number: Sequence number from Remote (for ordering)
            remote_stargate_id: Remote Stargate ID (required, fail-fast)
        """
        # Get or create gateway (shared helper)
        gateway = self._ensure_gateway(gateway_id, remote_stargate_id)

        # Always update liveness timestamp (poll proves connectivity)
        self._update_heartbeat_timestamp(gateway)

        # Reject out-of-order deltas (after timestamp update)
        if self._is_out_of_order(gateway, sequence_number):
            return

        # Handle empty deltas (just sequence number update)
        if len(delta) == 0:
            logger.debug(f"Empty delta for {gateway_id}: seq={sequence_number}")
            gateway._last_sequence_number = sequence_number
            return

        # Resource-bearing delta: refresh resource freshness
        self._update_telemetry_timestamps(gateway)

        # Compute updates from delta
        updates = self._compute_delta_updates(gateway, delta)

        # Commit updates
        self._commit_gateway_update(gateway_id, gateway, updates, sequence_number)

    def _is_out_of_order(self, gateway: FederatedGateway, sequence_number: int) -> bool:
        """
        Check if delta is duplicate or out-of-order (should be skipped).

        Duplicate sequence numbers are expected after delivery - Remote waits
        for Master to poll twice at same sequence to confirm delivery, then
        clears accumulator.

        Returns:
            True if should skip (duplicate or out-of-order), False if valid
        """
        # Sequence -1 is heartbeat sentinel (always allowed)
        if sequence_number == -1:
            return False

        last_sequence = getattr(gateway, "_last_sequence_number", 0)
        if sequence_number <= last_sequence:
            if sequence_number == last_sequence:
                # Duplicate sequence - Remote is waiting for delivery confirmation
                logger.debug(
                    f"Duplicate delta for {gateway.gateway_id}: seq={sequence_number} "
                    f"(Remote will clear accumulator after this poll)"
                )
            else:
                # Truly out-of-order (shouldn't happen with HTTP polling)
                logger.warning(
                    f"Out-of-order delta for {gateway.gateway_id}: "
                    f"received={sequence_number}, last={last_sequence} (skipping)"
                )
            return True
        return False

    def _compute_delta_updates(
        self, gateway: FederatedGateway, delta: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Compute state updates from delta payload (pure function).

        Handles both:
        - Delta format: {"added": [...], "removed": [...]}
        - Full list format: [...]
        """
        updates: dict[str, Any] = {}

        # Handle model list fields with added/removed format
        if "loaded_models" in delta:
            updates["loaded_models"] = self._apply_model_list_delta(
                gateway.loaded_models, delta["loaded_models"]
            )

        if "busy_models" in delta:
            updates["busy_models"] = self._apply_model_list_delta(
                gateway.busy_models, delta["busy_models"]
            )

        if "available_models" in delta:
            updates["available_models"] = self._apply_model_list_delta(
                gateway.available_models, delta["available_models"]
            )

        # Handle scalar fields
        for field in (
            "active_requests",
            "vram_free_mb",
            "ram_free_mb",
        ):
            if field in delta:
                updates[field] = delta[field]

        return updates

    def _apply_model_list_delta(
        self,
        current: frozenset[ModelId],
        value: dict[str, Any] | list[str],
    ) -> frozenset[ModelId]:
        """
        Apply delta to a model list field.

        Args:
            current: Current model set
            value: Delta value (dict with added/removed, or full list)

        Returns:
            Updated frozenset of ModelId
        """
        if isinstance(value, dict) and "added" in value and "removed" in value:
            # Delta format: merge added/removed
            result = set(current)
            result.update(ModelId.parse(m) for m in value["added"])
            result.difference_update(ModelId.parse(m) for m in value["removed"])
            return frozenset(result)
        else:
            # Full list format (snapshot or reconnect)
            return frozenset(ModelId.parse(m) for m in value)

    def _commit_gateway_update(
        self,
        gateway_id: str,
        gateway: FederatedGateway,
        updates: dict[str, Any],
        sequence_number: int,
    ) -> None:
        """
        Commit gateway state updates with sequence number tracking.

        Args:
            gateway_id: Gateway identifier
            gateway: Current gateway instance
            updates: Field updates to apply
            sequence_number: Sequence number to record
        """
        if updates:
            # Detect catalog changes (available_models)
            old_catalog = gateway.available_models
            catalog_changed = False

            if "available_models" in updates:
                new_catalog = updates["available_models"]
                catalog_changed = old_catalog != new_catalog

            updated_gateway = replace(gateway, **updates)
            updated_gateway._last_sequence_number = sequence_number
            self._gateways[gateway_id] = updated_gateway

            # Log detailed changes for list fields
            change_details = []
            for field, value in updates.items():
                if field in ("loaded_models", "busy_models", "available_models"):
                    change_details.append(f"{field}={len(value)}")
                else:
                    change_details.append(f"{field}={value}")

            logger.info(
                f"✅ Applied delta to {gateway_id}: seq={sequence_number}, "
                f"updates=[{', '.join(change_details)}]"
            )

            # Publish catalog change event (CRITICAL for pipeline reload)
            if catalog_changed:
                logger.info(
                    f"📦 Catalog changed for gateway {gateway_id}: "
                    f"{len(old_catalog)} → {len(new_catalog)} models"
                )
                import asyncio

                from src.scheduling.events import FederationGatewayCatalogChanged

                asyncio.create_task(
                    self._event_bus.publish_async_nowait(
                        FederationGatewayCatalogChanged(
                            gateway_id=gateway_id,
                            old_model_count=len(old_catalog),
                            new_model_count=len(new_catalog),
                        )
                    )
                )
        else:
            # No field updates, just update sequence number
            gateway._last_sequence_number = sequence_number

    @sequential
    async def apply_snapshot(
        self,
        gateway_id: str,
        snapshot: dict[str, Any],
        *,
        remote_stargate_id: str,
    ) -> None:
        """
        Apply full telemetry snapshot (HTTP polling path).

        INVARIANT: ∀ snapshot_receipt ⟹ timestamps_updated (freshness)

        Args:
            gateway_id: Gateway to update
            snapshot: Complete state snapshot from Remote
            remote_stargate_id: Remote Stargate ID (required, fail-fast)
        """
        # Get or create gateway (shared helper)
        gateway = self._ensure_gateway(gateway_id, remote_stargate_id)

        # Snapshot is resource-bearing: refresh resource freshness + liveness
        self._update_telemetry_timestamps(gateway)

        # Extract sequence number
        sequence_number = snapshot.get("sequence_number", 0)

        # Compute updates from snapshot
        updates = self._compute_snapshot_updates(snapshot)

        # Commit updates
        self._commit_gateway_update(gateway_id, gateway, updates, sequence_number)

        logger.info(
            f"Applied full snapshot to {gateway_id}: seq={sequence_number}, "
            f"fields={list(updates.keys())}"
        )

    def _compute_snapshot_updates(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """
        Compute state updates from snapshot payload (pure function).

        NOTE: HTTP polling snapshots include loaded_models/busy_models in the snapshot
        (not as separate discrete events). This is different from WebSocket telemetry
        where discrete MODEL_LOADED/MODEL_UNLOADED events are sent separately.

        For HTTP polling:
        - Full snapshots (seq=0) are authoritative (initial state or reconnect)
        - Incremental deltas are preferred (apply_delta uses delta computation)

        CONSIDERATION: If HTTP polling adds discrete model events in the future,
        this should be updated to match _apply_resource_update behavior
        (preserve loaded_models/busy_models, update only via discrete events).

        Args:
            snapshot: Complete state snapshot

        Returns:
            Field updates to apply to gateway
        """
        updates: dict[str, Any] = {}

        if "loaded_models" in snapshot:
            updates["loaded_models"] = frozenset(
                ModelId.parse(m) for m in snapshot["loaded_models"]
            )

        if "busy_models" in snapshot:
            updates["busy_models"] = frozenset(
                ModelId.parse(m) for m in snapshot["busy_models"]
            )

        if "available_models" in snapshot:
            updates["available_models"] = frozenset(
                ModelId.parse(m) for m in snapshot["available_models"]
            )

        for field in (
            "active_requests",
            "vram_free_mb",
            "ram_free_mb",
        ):
            if field in snapshot:
                updates[field] = snapshot[field]

        return updates
