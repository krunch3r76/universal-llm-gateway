"""Gateway WebSocket state management.

Cached state from INIT message + real-time updates via event handlers.
"""

from typing import Any

from universal_logging import get_logger

from ..messages import InitData, ResourcesData

logger = get_logger(__name__)


class GatewayState:
    """
    Cached state container for Gateway WebSocket client.

    State is initialized from INIT message and updated by event handlers.
    Event-driven: handlers mutate these sets directly via HandlerContext.

    Invariant: ∀ state field, ∃! update path (via handlers or _process_init)
    """

    def __init__(self) -> None:
        # Cached state from INIT + updates
        self._init_data: InitData | None = None
        self._models: set[str] = set()
        self._loaded_models: set[str] = set()
        self._busy_models: set[str] = set()
        self._loading_models: set[str] = set()
        self._resources: ResourcesData = ResourcesData()
        self._catalog: dict[str, Any] = {}
        self._model_last_inference: dict[str, float] = {}  # model_id -> timestamp
        self._model_details: dict[str, dict[str, Any]] = {}  # model_id -> resources

        # Separate timestamps for heartbeat vs resource freshness
        self._last_heartbeat: float = 0.0  # Last heartbeat (liveness)
        self._last_resource_update: float = 0.0  # Last resource telemetry (capacity)

        # Reservation ledger (explicit tracking)
        # Invariant: effective_available = max(0, gateway_reported - reserved)
        self._reserved_vram_mb: int = 0
        self._reserved_ram_mb: int = 0
        self._last_gateway_vram_mb: int = 0  # Last RESOURCE_UPDATE from Gateway
        self._last_gateway_ram_mb: int = 0

    # =========================================================================
    # State Initialization
    # =========================================================================

    def process_init(self, data: dict[str, Any]) -> None:
        """
        Process INIT message and cache state.

        Resets all state from Gateway's authoritative snapshot.
        Called once per connection after INIT message received.
        """
        import time

        self._init_data = InitData.from_dict(data)
        self._models = set(self._init_data.models)
        self._loaded_models = set(self._init_data.loaded_models)
        self._catalog = self._init_data.catalog
        self._resources = ResourcesData.from_dict(self._init_data.resources)

        # Reset event-driven state from INIT snapshot
        self._busy_models = set(self._resources.busy_models)
        self._loading_models = set()  # Clear loading state on reconnect
        self._model_last_inference = {}  # Clear inference cache on reconnect
        self._model_details = {}  # Clear model details on reconnect

        # Reset reservation ledger (INIT is authoritative, no pending reservations)
        self._reserved_vram_mb = 0
        self._reserved_ram_mb = 0
        self._last_gateway_vram_mb = self._resources.available_vram_mb
        self._last_gateway_ram_mb = self._resources.available_ram_mb

        # Freshness: INIT is an authoritative resource snapshot.
        now = time.time()
        self._last_resource_update = now
        self._last_heartbeat = now

    # =========================================================================
    # State Access (Read-Only, Instant)
    # =========================================================================

    @property
    def gateway_name(self) -> str:
        """Gateway name from INIT message."""
        return self._init_data.gateway_name if self._init_data else "unknown"

    @property
    def gateway_version(self) -> str:
        """Gateway version from INIT message."""
        return self._init_data.version if self._init_data else "unknown"

    def get_models(self) -> set[str]:
        """Get available model IDs (instant, from cache)."""
        return self._models.copy()

    def get_resources(self) -> ResourcesData:
        """Get resource status (instant, from cache)."""
        return self._resources

    def get_catalog(self) -> dict[str, Any]:
        """Get catalog data (instant, from cache)."""
        return self._catalog.copy()

    def get_activated_contexts(self) -> dict[str, dict]:
        """Get activated contexts from catalog (instant, from cache)."""
        return self._catalog.get("activated_contexts", {})

    def get_transformations(self) -> dict[str, Any]:
        """Get catalog transformations (instant, from cache)."""
        return self._catalog.get("transformations", {})

    def get_resource_status(self, is_connected: bool) -> ResourcesData | None:
        """
        Get current resource status from real-time WebSocket state.

        Args:
            is_connected: Whether WebSocket is currently connected

        Returns:
            ResourcesData with metrics and model state, or None if disconnected.

        Event-driven: automatically updated on RESOURCE_UPDATE/MODEL_LOADED/
        UNLOADED events.
        This is the ONLY source of resource status - no HTTP fallback.
        """
        if not is_connected:
            return None

        # Return complete status with all fields populated from event-driven state
        result = ResourcesData(
            total_ram_mb=self._resources.total_ram_mb,
            available_ram_mb=self._resources.available_ram_mb,
            total_vram_mb=self._resources.total_vram_mb,
            available_vram_mb=self._resources.available_vram_mb,
            loaded_models=frozenset(self._loaded_models),
            busy_models=frozenset(self._busy_models),
            model_details=self._model_details.copy(),
            model_last_inference=self._model_last_inference.copy(),
        )
        return result

    def get_loaded_models(self) -> frozenset[str]:
        """
        Get current set of loaded models from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_LOADED/MODEL_UNLOADED events.
        """
        return frozenset(self._loaded_models)

    def get_busy_models(self) -> frozenset[str]:
        """
        Get current set of busy models from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_BUSY/MODEL_IDLE events.
        """
        return frozenset(self._busy_models)

    def get_loading_models(self) -> frozenset[str]:
        """
        Get current set of models currently loading from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_LOADING_STARTED/
        MODEL_LOADED events.
        """
        return frozenset(self._loading_models)

    def get_model_last_inference_time(self, model_id: str) -> float | None:
        """Get last inference time for a model (instant, from cache)."""
        return self._model_last_inference.get(model_id)

    def get_all_model_last_inference(self) -> dict[str, float]:
        """Get all cached last inference times (instant, from cache)."""
        return self._model_last_inference.copy()

    def update_heartbeat_timestamp(self) -> None:
        """Update heartbeat timestamp (called on TELEMETRY_HEARTBEAT only)."""
        import time

        self._last_heartbeat = time.time()

    def update_resource_timestamp(self) -> None:
        """
        Update resource freshness timestamp.

        Called on RESOURCE_UPDATE, MODEL_* events.
        """
        import time

        now = time.time()
        self._last_resource_update = now
        self._last_heartbeat = now  # Resource events also prove liveness

    @property
    def last_heartbeat(self) -> float:
        """Last heartbeat timestamp."""
        return self._last_heartbeat

    @property
    def last_resource_update(self) -> float:
        """Last resource update timestamp."""
        return self._last_resource_update

    def is_telemetry_healthy(self, ttl_seconds: float = 60.0) -> bool:
        """Check if telemetry path is functioning (any signal received)."""
        import time

        last_signal = max(self._last_heartbeat, self._last_resource_update)
        return time.time() - last_signal < ttl_seconds

    def is_resource_fresh(self, ttl_seconds: float = 60.0) -> bool:
        """Check if resource data is fresh enough for scheduling."""
        import time

        return time.time() - self._last_resource_update < ttl_seconds

    # =========================================================================
    # State Mutation (Internal - Used by Handlers via Context)
    # =========================================================================

    @property
    def models(self) -> set[str]:
        """Mutable reference for handlers."""
        return self._models

    @property
    def loaded_models(self) -> set[str]:
        """Mutable reference for handlers."""
        return self._loaded_models

    @property
    def busy_models(self) -> set[str]:
        """Mutable reference for handlers."""
        return self._busy_models

    @property
    def loading_models(self) -> set[str]:
        """Mutable reference for handlers."""
        return self._loading_models

    @property
    def catalog(self) -> dict[str, Any]:
        """Mutable reference for handlers."""
        return self._catalog

    @property
    def resources(self) -> ResourcesData:
        """Current resources (read-only access)."""
        return self._resources

    @property
    def model_last_inference(self) -> dict[str, float]:
        """Mutable reference for handlers."""
        return self._model_last_inference

    @property
    def model_details(self) -> dict[str, dict[str, Any]]:
        """Mutable reference for handlers."""
        return self._model_details

    def apply_reservation(self, *, vram_mb: int, ram_mb: int) -> None:
        """
        Apply resource reservation by incrementing reservation counters.

        Called when RESOURCE_RESERVED event is received.

        Args:
            vram_mb: VRAM to reserve (keyword-only)
            ram_mb: RAM to reserve (keyword-only)
        """
        self._reserved_vram_mb += vram_mb
        self._reserved_ram_mb += ram_mb
        self._recompute_effective_availability()

    def release_reservation(self, *, vram_mb: int, ram_mb: int) -> None:
        """
        Release resource reservation by decrementing reservation counters.

        Called when RESOURCE_RELEASED event is received.

        Args:
            vram_mb: VRAM to release (keyword-only)
            ram_mb: RAM to release (keyword-only)
        """
        self._reserved_vram_mb = max(0, self._reserved_vram_mb - vram_mb)
        self._reserved_ram_mb = max(0, self._reserved_ram_mb - ram_mb)
        self._recompute_effective_availability()

    def update_resources_from_gateway(
        self,
        *,
        available_vram_mb: int | None,
        available_ram_mb: int | None,
    ) -> None:
        """
        Update resources from Gateway's RESOURCE_UPDATE message.

        Stores Gateway-reported values and recomputes effective availability
        by subtracting active reservations.

        Invariant: effective_available = max(0, gateway_reported - reserved)

        Args:
            available_vram_mb: Gateway's reported available VRAM (keyword-only)
            available_ram_mb: Gateway's reported available RAM (keyword-only)
        """
        if available_vram_mb is not None:
            self._last_gateway_vram_mb = available_vram_mb
        if available_ram_mb is not None:
            self._last_gateway_ram_mb = available_ram_mb

        self._recompute_effective_availability()

    def _recompute_effective_availability(self) -> None:
        """
        Recompute effective available resources from Gateway-reported and reservations.

        Called after:
        - RESOURCE_UPDATE (gateway_reported changes)
        - RESOURCE_RESERVED (reserved increases)
        - RESOURCE_RELEASED (reserved decreases)
        """
        effective_vram, effective_ram = self._compute_effective_available()
        self._log_reservation_impact(effective_vram, effective_ram)
        self._set_effective_resources(effective_vram, effective_ram)

    def _compute_effective_available(self) -> tuple[int, int]:
        """
        Compute effective available resources.

        Returns:
            (effective_vram_mb, effective_ram_mb)

        Invariant: effective = max(0, gateway_reported - reserved)
        """
        effective_vram = max(0, self._last_gateway_vram_mb - self._reserved_vram_mb)
        effective_ram = max(0, self._last_gateway_ram_mb - self._reserved_ram_mb)
        return effective_vram, effective_ram

    def _set_effective_resources(self, effective_vram: int, effective_ram: int) -> None:
        """Update _resources with computed effective availability."""
        self._resources = ResourcesData(
            total_ram_mb=self._resources.total_ram_mb,
            available_ram_mb=effective_ram,
            total_vram_mb=self._resources.total_vram_mb,
            available_vram_mb=effective_vram,
            loaded_models=self._resources.loaded_models,
            busy_models=self._resources.busy_models,
            model_details=self._resources.model_details,
            model_last_inference=self._resources.model_last_inference,
        )

    def _log_reservation_impact(self, effective_vram: int, effective_ram: int) -> None:
        """Log when reservations reduce effective availability."""
        if self._reserved_vram_mb > 0 and effective_vram < self._last_gateway_vram_mb:
            logger.debug(
                "resource_reservation_active: "
                f"gateway_vram={self._last_gateway_vram_mb}MB "
                f"reserved={self._reserved_vram_mb}MB "
                f"effective={effective_vram}MB"
            )
        if self._reserved_ram_mb > 0 and effective_ram < self._last_gateway_ram_mb:
            logger.debug(
                "resource_reservation_active: "
                f"gateway_ram={self._last_gateway_ram_mb}MB "
                f"reserved={self._reserved_ram_mb}MB "
                f"effective={effective_ram}MB"
            )
