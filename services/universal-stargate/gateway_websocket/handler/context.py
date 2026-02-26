"""Handler context providing access to client state and side-effect schedulers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..messages import ResourcesData

if TYPE_CHECKING:
    from .telemetry import ComputeCapacityTelemetryHandler


@dataclass
class HandlerContext:
    """
    Context passed to message handlers.

    Provides:
    - State accessors (read/write model sets)
    - Side-effect schedulers (callbacks, events)
    - Gateway metadata

    Design:
    - Mutable sets passed by reference for state mutation
    - Callbacks scheduled fire-and-forget via schedule_callback()
    - No direct await on external callbacks (non-blocking)

    Invariant: ∀ handler, ctx provides ∃! path to state mutation
    """

    # State (mutable references - handlers mutate these directly)
    loaded_models: set[str]
    loading_models: set[str]
    busy_models: set[str]
    models: set[str]
    catalog: dict[str, Any]
    model_last_inference: dict[str, float] = field(default_factory=dict)
    model_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    busy_since: dict[str, float] = field(default_factory=dict)

    # Resources state (read-only, updated via reservation-aware setter)
    _resources: ResourcesData = field(default_factory=ResourcesData)
    _resources_from_gateway_setter: Callable[[int | None, int | None], None] | None = (
        None
    )

    # Metadata
    gateway_name: str = ""
    gateway_http_url: str = ""

    # Side-effect schedulers (fire-and-forget)
    schedule_callback: Callable[[Callable, tuple], None] = field(
        default=lambda cb, args: None
    )
    schedule_capacity_freed: Callable[[str], None] = field(default=lambda m: None)

    # I/O (for async handlers only - PING)
    send_message: Callable[[str], Awaitable[None]] | None = None

    # Query handling
    pending_queries: dict[str, Any] = field(default_factory=dict)

    # Callbacks (scheduled fire-and-forget, never awaited in handlers)
    # Global callbacks - called for ALL models
    on_model_loading_started: Callable[[str], Awaitable[None]] | None = None
    on_model_loaded: Callable[[str, dict], Awaitable[None]] | None = None
    on_model_unloaded: Callable[[str], Awaitable[None]] | None = None
    on_model_load_failed: Callable[[str, str], Awaitable[None]] | None = None
    on_model_busy: Callable[[str], Awaitable[None]] | None = None
    on_model_idle: Callable[[str, dict], Awaitable[None]] | None = None
    on_resource_update: Callable[[dict], Awaitable[None]] | None = None
    on_catalog_update: Callable[[dict], Awaitable[None]] | None = None
    on_heartbeat: Callable[[], Awaitable[None]] | None = None
    on_resource_change: Callable[[], Awaitable[None]] | None = None
    on_telemetry_heartbeat: Callable[[dict], Awaitable[None]] | None = None

    # Model-specific callbacks (keyed by routing_key, multiple callbacks per key)
    # Used by LoadOutcomeTracker for concurrent load tracking without race conditions
    # Stores sets to support multiple trackers waiting for the same model
    model_loaded_callbacks: dict[str, set[Callable[[str, dict], Awaitable[None]]]] = (
        field(default_factory=dict)
    )
    model_load_failed_callbacks: dict[
        str, set[Callable[[str, str], Awaitable[None]]]
    ] = field(default_factory=dict)

    # Telemetry handler (per-gateway capacity telemetry)
    _capacity_telemetry_handler: ComputeCapacityTelemetryHandler | None = None

    @property
    def resources(self) -> ResourcesData:
        """Get current resources (read-only access)."""
        return self._resources

    def update_resources_from_gateway(
        self,
        *,
        available_vram_mb: int | None = None,
        available_ram_mb: int | None = None,
    ) -> None:
        """
        Update resources from Gateway's RESOURCE_UPDATE (reservation-aware).

        Delegates to GatewayState.update_resources_from_gateway() which computes
        effective availability as: effective = gateway_reported - reserved

        Args:
            available_vram_mb: Gateway-reported VRAM (keyword-only)
            available_ram_mb: Gateway-reported RAM (keyword-only)
        """
        if self._resources_from_gateway_setter:
            self._resources_from_gateway_setter(
                available_vram_mb=available_vram_mb,
                available_ram_mb=available_ram_mb,
            )

    def get_capacity_telemetry_handler(self) -> ComputeCapacityTelemetryHandler | None:
        """Get capacity telemetry handler for this gateway."""
        return self._capacity_telemetry_handler

    def set_capacity_telemetry_handler(
        self, handler: ComputeCapacityTelemetryHandler
    ) -> None:
        """Set capacity telemetry handler for this gateway."""
        self._capacity_telemetry_handler = handler
