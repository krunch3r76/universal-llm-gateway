"""
Global model load coordinator.

Coordinates model loading across all gateways to ensure single-writer semantics.
"""

from __future__ import annotations

import asyncio
import time

from model_id import ModelId
from universal_event_bus.actor import Sequential, sequential
from universal_logging import get_logger

from .local_coordinator import LoadCoordinationResult

logger = get_logger(__name__)


class GlobalModelLoadCoordinator(Sequential):
    """
    Coordinates model loading across gateways.

    Invariant: ∀ routing_key, ∃! gateway where loaded(routing_key, gateway)
    """

    def __init__(self):
        super().__init__()
        # Model tracking: routing_key -> gateway
        self._models_loaded: dict[str, str] = {}

        # Loading state
        self._models_loading: dict[str, tuple[str, asyncio.Event]] = {}

        # Error state tracking (routing_key -> (gateway, error_message))
        self._models_error: dict[str, tuple[str, str]] = {}

        # Reservations for routing coordination
        self._routing_reservations: dict[str, tuple[str, float]] = {}
        self._routing_reservation_waiters: dict[str, list[asyncio.Event]] = {}

        # Coordinator-verified loads: canonical model_id -> timestamp of verification
        # Tracks loads that WE coordinated (not external/discovered)
        # Uses canonical model_id from ModelId library to distinguish context variants
        # Example: "model-8192-hybrid" vs "model-4096" are tracked separately
        self._coordinator_verified: dict[str, float] = {}

    def clear_all_state(self) -> None:
        """
        Clear all coordinator state.

        Use on startup to prevent stale entries from previous runs.
        Must be called before any coordination operations.
        """
        self._models_loaded.clear()
        self._models_loading.clear()
        self._models_error.clear()
        self._routing_reservations.clear()
        self._routing_reservation_waiters.clear()
        self._coordinator_verified.clear()
        logger.info("🧹 Cleared all coordinator state")

    def _get_routing_key(self, model: str) -> str:
        """
        Extract routing key from model ID.

        This is the single source of truth for model identity.
        """
        try:
            parsed = ModelId.parse(model)
            return parsed.routing_key
        except ValueError:
            # Fallback for malformed IDs (shouldn't happen after validation)
            return model

    # -------------------------------------------------------------------------
    # Main coordination (sequential - runs one at a time)
    # -------------------------------------------------------------------------

    @sequential
    async def request_model_load(
        self,
        parsed: ModelId,
        target_gateway: str,
    ) -> LoadCoordinationResult:
        """
        Request to load a model on a gateway.

        Args:
            parsed: Parsed model ID with routing_key
            target_gateway: Gateway to load on

        Returns:
            LoadCoordinationResult with decision and any redirect/wait info
        """
        return await self._handle_load(parsed.routing_key, target_gateway)

    async def _handle_load(
        self, routing_key: str, target_gateway: str
    ) -> LoadCoordinationResult:
        """
        Handle model load request.

        Invariant: Only one worker globally per routing_key.
        """
        # Already loaded?
        if routing_key in self._models_loaded:
            loaded_on = self._models_loaded[routing_key]
            if loaded_on != target_gateway:
                logger.warning(
                    f"🚫 {routing_key} already on {loaded_on}, not {target_gateway}"
                )
                return LoadCoordinationResult(
                    should_load=False,
                    redirect_gateway=loaded_on,
                )
            # Same gateway - already loaded there
            return LoadCoordinationResult(should_load=False, redirect_gateway=loaded_on)

        # Currently loading?
        if routing_key in self._models_loading:
            loading_on, event = self._models_loading[routing_key]
            return LoadCoordinationResult(
                should_load=False,
                redirect_gateway=loading_on,
                wait_event=event,
            )

        # Start load
        event = asyncio.Event()
        self._models_loading[routing_key] = (target_gateway, event)
        logger.info(f"🔒 {routing_key} load starting on {target_gateway}")
        return LoadCoordinationResult(should_load=True)

    @sequential
    async def report_load_complete(
        self,
        parsed: ModelId,
        gateway: str,
        succeeded: bool,
    ) -> None:
        """Report that a model load has completed."""
        routing_key = parsed.routing_key

        if routing_key in self._models_loading:
            _, event = self._models_loading.pop(routing_key)
            event.set()

        if succeeded:
            self._models_loaded[routing_key] = gateway
            logger.info(f"🔓 {routing_key} loaded on {gateway}")
        else:
            _ = self._models_loaded.pop(routing_key, None)

    @sequential
    async def report_unload(
        self,
        parsed: ModelId,
        gateway: str,
    ) -> None:
        """Report that a model has been unloaded."""
        routing_key = parsed.routing_key
        _ = self._models_loaded.pop(routing_key, None)
        logger.info(f"🗑️ {routing_key} unloaded from {gateway}")

    def get_model_gateway(self, routing_key: str) -> str | None:
        """Get gateway where model is loaded."""
        return self._models_loaded.get(routing_key)

    # -------------------------------------------------------------------------
    # Routing reservations (delegated to reservations.py)
    # -------------------------------------------------------------------------

    @sequential
    async def reserve_for_routing(
        self, model: str, requester_id: str, ttl_seconds: float = 5.0
    ) -> tuple[bool, str | None, asyncio.Event | None]:
        """
        Reserve a model for routing decision (short-lived, auto-expires).

        Prevents parallel pipeline steps from racing to select the same gateway.
        Called BEFORE gateway selection, not after.

        Invariant: ∀ routing_key, ∃! reservation holder (or none)

        Args:
            model: Model ID to reserve
            requester_id: Unique ID for this routing request (e.g., "pipeline-abc123")
            ttl_seconds: Reservation TTL (auto-expires to prevent deadlocks)

        Returns:
            (can_reserve, redirect_gateway, wait_event)
            - (True, None, None): Reservation granted, proceed with routing
            - (False, gateway_name, event): Model loading on gateway, wait for event
            - (False, None, event): Another reservation active, wait for event
        """
        from .reservations import reserve_for_routing

        return await reserve_for_routing(self, model, requester_id, ttl_seconds)

    @sequential
    async def release_routing_reservation(self, model: str, requester_id: str) -> None:
        """
        Release a routing reservation.

        Called after gateway selection completes (success or failure).
        Safe to call even if reservation expired or was never held.
        Wakes up any waiters (event-driven).
        """
        from .reservations import release_routing_reservation

        await release_routing_reservation(self, model, requester_id)

    # -------------------------------------------------------------------------
    # Pipeline eviction protection
    # -------------------------------------------------------------------------

    @sequential
    async def add_eviction_protection(
        self, model: str, requester_id: str, ttl_seconds: float = 300.0
    ) -> None:
        """
        Add eviction protection for a model used by a pipeline step.

        Unlike reserve_for_routing(), this does NOT check if the model is
        already loaded. It unconditionally adds an entry to prevent eviction.

        Use case: Pipeline steps acquire models before executing. During
        execution, other steps may trigger eviction to load different models.
        This protection prevents evicting models that are actively in use.

        Args:
            model: Model ID to protect
            requester_id: Unique ID for this protection (e.g., "pipeline-abc-step1")
            ttl_seconds: Protection TTL (auto-expires to prevent leaks)
        """
        from .reservations import add_eviction_protection

        await add_eviction_protection(self, model, requester_id, ttl_seconds)

    @sequential
    async def remove_eviction_protection(self, model: str, requester_id: str) -> None:
        """
        Remove eviction protection for a model.

        Called when a pipeline step completes. Safe to call if protection
        was never added or already removed.

        Args:
            model: Model ID to unprotect
            requester_id: Requester that added the protection
        """
        from .reservations import remove_eviction_protection

        await remove_eviction_protection(self, model, requester_id)

    # -------------------------------------------------------------------------
    # Read-only queries (no sequentiality needed)
    # -------------------------------------------------------------------------

    def where_is_loading(self, model: str) -> str | None:
        """Which gateway is currently loading this model?"""
        routing_key = self._get_routing_key(model)
        entry = self._models_loading.get(routing_key)
        return entry[0] if entry else None

    def where_is_loaded(self, model: str) -> str | None:
        """Which gateway has this model loaded?"""
        routing_key = self._get_routing_key(model)
        return self._models_loaded.get(routing_key)

    def get_error_state(self, model: str) -> tuple[str, str] | None:
        """
        Get error state for model if in ERROR.

        Returns:
            (gateway_name, error_message) if model is in ERROR state, None otherwise
        """
        routing_key = self._get_routing_key(model)
        return self._models_error.get(routing_key)

    def is_routing_reserved(self, model: str) -> bool:
        """
        Check if model has active routing reservation.

        Read-only, no @sequential needed.
        """
        routing_key = self._get_routing_key(model)
        if routing_key not in self._routing_reservations:
            return False
        _, expiry_time = self._routing_reservations[routing_key]
        return time.time() < expiry_time

    def get_routing_keys_with_reservations(self) -> set[str]:
        """
        Get all routing keys with active (non-expired) reservations.

        Used by eviction logic to avoid evicting models that are reserved.

        Returns:
            Set of routing keys with active reservations
        """
        current_time = time.time()
        return {
            routing_key
            for routing_key, (_, expiry_time) in self._routing_reservations.items()
            if current_time < expiry_time
        }

    def was_load_coordinated(self, model_id: ModelId, max_age: float = 600.0) -> bool:
        """Check if a model load was coordinated by this instance."""
        from .event_sync import was_load_coordinated

        return was_load_coordinated(self, model_id, max_age)

    def clear_verified_state_for_gateway(self, gateway_name: str) -> None:
        """
        Clear coordinator-verified state for a gateway on reconnection.

        Called when WebSocket reconnects to ensure we don't trust stale
        coordinator-verified state (may have missed events during disconnection).

        Pre: WebSocket reconnecting/reconnected
        Post: ∀ routing_key where _models_loaded[routing_key] = gateway_name:
              _coordinator_verified[routing_key] = None (cleared)

        Args:
            gateway_name: Name of the gateway that reconnected
        """
        from .event_sync import clear_verified_state_for_gateway

        self._executor.run_nowait(clear_verified_state_for_gateway(self, gateway_name))

    # -------------------------------------------------------------------------
    # Event-driven updates (fire-and-forget)
    # -------------------------------------------------------------------------

    def on_model_loading_started_event(self, model: str, gateway: str) -> None:
        """Handle MODEL_LOADING_STARTED event from WebSocket."""
        from model_id import ModelId

        from .event_sync import mark_as_loading_from_event

        model_id = ModelId.parse(model)
        self._executor.run_nowait(mark_as_loading_from_event(self, model_id, gateway))

    def on_model_loaded_event(self, model: str, gateway: str) -> None:
        """Handle MODEL_LOADED event from WebSocket."""
        from model_id import ModelId

        from .event_sync import mark_as_loaded

        model_id = ModelId.parse(model)
        self._executor.run_nowait(mark_as_loaded(self, model_id, gateway))

    def on_model_unloaded_event(self, model: str) -> None:
        """Handle MODEL_UNLOADED event from WebSocket."""
        from model_id import ModelId

        from .event_sync import mark_as_unloaded

        model_id = ModelId.parse(model)
        self._executor.run_nowait(mark_as_unloaded(self, model_id))

    def on_model_load_failed_event(
        self, model: str, gateway: str, error_message: str
    ) -> None:
        """Handle MODEL_LOAD_FAILED event from WebSocket."""
        from model_id import ModelId

        from .event_sync import mark_as_error

        model_id = ModelId.parse(model)
        self._executor.run_nowait(mark_as_error(self, model_id, gateway, error_message))

    def sync_loaded_models_for_gateway(
        self, gateway_name: str, loaded_models: frozenset[str]
    ) -> None:
        """
        Sync coordinator state with gateway's currently loaded models.

        Called on WebSocket connect/reconnect to ensure coordinator
        knows about models loaded before Stargate started or during
        WebSocket disconnection.

        Pre: gateway WebSocket connected, INIT message processed
        Post: ∀ model ∈ loaded_models: coordinator.where_is_loaded(model) = gateway_name

        Args:
            gateway_name: Name of the gateway
            loaded_models: Set of model IDs currently loaded on this gateway
        """
        from .event_sync import sync_loaded_models

        self._executor.run_nowait(sync_loaded_models(self, gateway_name, loaded_models))

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the coordinator."""
        await self._start_executor()

    async def stop(self) -> None:
        """Stop the coordinator."""
        await self._stop_executor()
