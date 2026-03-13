"""Gateway WebSocket state management.

Cached state from INIT message + real-time updates via event handlers.
"""

import time
from typing import Any

from universal_logging import get_logger

from ..messages import InitData, ResourcesData
from .state_watchdogs import expire_stale_busy_models, expire_stale_loading_models

logger = get_logger(__name__)


class GatewayState:
    """
    Cached state container for Gateway WebSocket client.

    State is initialized from INIT message and updated by event handlers.
    Event-driven: handlers mutate these sets directly via HandlerContext.

    Invariant: ∀ state field, ∃! update path (via handlers or _process_init)
    """

    BUSY_MODEL_TTL_SECONDS: float = 600.0  # 10 min — auto-clear stale busy state
    LOADING_MODEL_TTL_SECONDS: float = 300.0  # 5 min — auto-clear stuck loading state

    def __init__(self) -> None:
        # Cached state from INIT + updates
        self._init_data: InitData | None = None
        self._models: set[str] = set()
        self._loaded_models: set[str] = set()
        self._busy_models: set[str] = set()
        self._busy_since: dict[str, float] = {}  # model_id → monotonic timestamp
        self._loading_models: set[str] = set()
        self._loading_since: dict[str, float] = {}  # model_id → monotonic timestamp
        self._resources: ResourcesData = ResourcesData()
        self._catalog: dict[str, Any] = {}
        self._model_last_inference: dict[str, float] = {}  # model_id -> timestamp
        self._model_details: dict[str, dict[str, Any]] = {}  # model_id -> resources
        self._measured_model_vram: dict[
            str, int
        ] = {}  # model_id -> nvidia-smi-measured MB
        self._last_vram_drift_report: dict[str, float] = {}  # model_id -> monotonic ts

        # Separate timestamps for heartbeat vs resource freshness
        self._last_heartbeat: float = 0.0  # Last heartbeat (liveness)
        self._last_resource_update: float = 0.0  # Last resource telemetry (capacity)

        # Reservation ledger (explicit tracking)
        # Invariant: effective_available = max(0, gateway_reported - reserved)
        self._reserved_vram_mb: int = 0
        self._reserved_ram_mb: int = 0
        self._last_gateway_vram_mb: int = 0  # Last RESOURCE_UPDATE from Gateway
        self._last_gateway_ram_mb: int = 0

    def process_init(self, data: dict[str, Any]) -> None:
        """Process INIT message and refresh all cached state."""
        import time

        self._init_data = InitData.from_dict(data)
        self._models = set(self._init_data.models)
        self._loaded_models = set(self._init_data.loaded_models)
        self._catalog = self._init_data.catalog
        self._resources = ResourcesData.from_dict(self._init_data.resources)

        # Reset event-driven state from INIT snapshot
        import time as _time

        self._busy_models = set(self._resources.busy_models)
        now = _time.monotonic()
        self._busy_since = {m: now for m in self._busy_models}
        self._loading_models = set()  # Clear loading state on reconnect
        self._loading_since = {}  # Clear loading timestamps on reconnect
        self._model_last_inference = {}  # Clear inference cache on reconnect
        self._model_details = {}  # Clear model details on reconnect
        self._measured_model_vram = {}  # Clear measured VRAM on reconnect
        self._last_vram_drift_report = {}  # Reset cooldown on reconnect

        # Reset reservation ledger (INIT is authoritative, no pending reservations)
        self._reserved_vram_mb = 0
        self._reserved_ram_mb = 0
        self._last_gateway_vram_mb = self._resources.available_vram_mb
        self._last_gateway_ram_mb = self._resources.available_ram_mb

        # Freshness: INIT is an authoritative resource snapshot.
        now = time.time()
        self._last_resource_update = now
        self._last_heartbeat = now

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
        """Get current resource status from event-driven WebSocket state."""
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
        Self-healing: auto-clears models busy longer than BUSY_MODEL_TTL_SECONDS
        to prevent permanent routing lockup from lost MODEL_IDLE messages.
        """
        self._expire_stale_busy_models()
        return frozenset(self._busy_models)

    def get_loading_models(self) -> frozenset[str]:
        """
        Get current set of models currently loading from real-time WebSocket state.

        Event-driven: automatically updated on MODEL_LOADING_STARTED/
        MODEL_LOADED events.
        Self-healing: auto-clears models loading longer than
        LOADING_MODEL_TTL_SECONDS to prevent permanent VRAM reservation
        from lost MODEL_LOADED/MODEL_LOAD_FAILED messages.
        """
        self._expire_stale_loading_models()
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
        """Update resource freshness timestamp from resource-affecting events."""
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
        """Mutable reference for handlers. Handlers must also update _busy_since."""
        return self._busy_models

    @property
    def busy_since(self) -> dict[str, float]:
        """Mutable reference for busy timestamp tracking."""
        return self._busy_since

    def _expire_stale_busy_models(self) -> None:
        """Auto-clear busy models that exceeded the TTL without a MODEL_BUSY refresh."""
        expire_stale_busy_models(
            self._busy_models,
            self._busy_since,
            self.BUSY_MODEL_TTL_SECONDS,
        )

    @property
    def loading_models(self) -> set[str]:
        """Mutable reference for handlers."""
        return self._loading_models

    @property
    def loading_since(self) -> dict[str, float]:
        """Mutable reference for loading timestamp tracking."""
        return self._loading_since

    def _expire_stale_loading_models(self) -> None:
        """Auto-clear loading models that exceeded the TTL without completion."""
        expire_stale_loading_models(
            self._loading_models,
            self._loading_since,
            self.LOADING_MODEL_TTL_SECONDS,
        )

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

    @property
    def measured_model_vram(self) -> dict[str, int]:
        """Mutable reference for handlers.
        Populated only by RESOURCE_UPDATE model_vram."""
        return self._measured_model_vram

    _VRAM_DRIFT_COOLDOWN_SECONDS: float = 3600.0

    def can_report_vram_drift(self, model_id: str) -> bool:
        """Return True if VRAM drift for model_id may be reported."""
        now = time.monotonic()
        last = self._last_vram_drift_report.get(model_id, 0.0)
        if now - last > self._VRAM_DRIFT_COOLDOWN_SECONDS:
            self._last_vram_drift_report[model_id] = now
            return True
        return False

    def apply_reservation(self, *, vram_mb: int, ram_mb: int) -> None:
        """Apply reservation counters after RESOURCE_RESERVED."""
        self._reserved_vram_mb += vram_mb
        self._reserved_ram_mb += ram_mb
        self._recompute_effective_availability()

    def release_reservation(self, *, vram_mb: int, ram_mb: int) -> None:
        """Release reservation counters after RESOURCE_RELEASED."""
        self._reserved_vram_mb = max(0, self._reserved_vram_mb - vram_mb)
        self._reserved_ram_mb = max(0, self._reserved_ram_mb - ram_mb)
        self._recompute_effective_availability()

    def update_resources_from_gateway(
        self,
        *,
        available_vram_mb: int | None,
        available_ram_mb: int | None,
    ) -> None:
        """Update gateway-reported availability and recompute effective availability."""
        if available_vram_mb is not None:
            self._last_gateway_vram_mb = available_vram_mb
        if available_ram_mb is not None:
            self._last_gateway_ram_mb = available_ram_mb

        self._recompute_effective_availability()

    def _recompute_effective_availability(self) -> None:
        """Recompute effective availability from gateway values and reservations."""
        effective_vram, effective_ram = self._compute_effective_available()
        self._log_reservation_impact(effective_vram, effective_ram)
        self._set_effective_resources(effective_vram, effective_ram)

    def _compute_effective_available(self) -> tuple[int, int]:
        """Compute effective available VRAM/RAM after reservation subtraction."""
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
