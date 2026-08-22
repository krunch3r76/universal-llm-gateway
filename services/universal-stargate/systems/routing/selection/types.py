"""
Domain types for gateway selection: Placement, Gateway, ModelDetails.

Minimal type system (dataclasses + TypedDict) shared by collectors, feasibility,
scoring, and eviction planning across the routing selection package.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from model_id import ModelId


@dataclass(frozen=True)
class Placement:
    """Model placement requirements for gateway selection."""

    model_id: ModelId  # CHANGED: was str, now ModelId
    ram_mb: int
    vram_mb: int
    is_gpu: bool
    original_model_id: str | None = None  # Keep as str for display/logging
    context_length: int | None = None  # For config lookup
    endpoint_category: str = "generation"  # "generation" or "embedding"


class ModelDetails(TypedDict, total=False):
    """Per-model routing metadata: idle timing, footprints, concurrency caps."""

    last_inference_time: float | None
    vram_usage: int
    ram_usage: int
    status: str
    max_concurrent_requests: int


@dataclass
class Gateway:
    """
    Gateway snapshot for selection decisions.

    Immutable view of gateway state at decision time.
    """

    ref: Any  # The actual GatewayInstance object
    name: str
    ram_free_mb: int
    vram_free_mb: int
    ram_total_mb: int = 0
    vram_total_mb: int = 0
    loaded_models: frozenset[ModelId] = field(default_factory=frozenset)  # CHANGED
    busy_models: frozenset[ModelId] = field(default_factory=frozenset)  # CHANGED
    loading_models: frozenset[ModelId] = field(default_factory=frozenset)  # CHANGED
    available_models: frozenset[ModelId] = field(default_factory=frozenset)  # CHANGED
    """Models available in this gateway's catalog (can be loaded)."""

    remote_stargate_id: str | None = None  # For eviction protection tracking
    node_id: str = ""  # Canonical node identity for affinity matching

    # Model details for eviction scoring (populated when include_model_details=True)
    model_details: dict[ModelId, ModelDetails] = field(default_factory=dict)
    """
    Model details including last_inference_time and resource usage.

    Populated by collect_gateways() from WebSocket state (MODEL_LOADED, MODEL_IDLE
    events) with fallback to model configuration catalog. Avoids blocking HTTP calls.

    Used by eviction planning to calculate staleness scores.

    Structure: {
        model_id: {
            "last_inference_time": float | None,
            "vram_usage": int,  # MB, from cache or catalog
            "ram_usage": int,   # MB, from cache or catalog
            "status": str,      # "busy" or "loaded"
        }
    }
    """
    model_measured_vram: dict[ModelId, int] = field(default_factory=dict)
    """nvidia-smi-measured VRAM per loaded model.
    Populated from RESOURCE_UPDATE model_vram. Empty until first RESOURCE_UPDATE.
    Eviction planner prefers this over model_details."""

    # Eviction hysteresis: monotonic timestamp when each model was loaded.
    # Populated from FederatedGateway.model_loaded_at via collector.
    model_loaded_at: dict[ModelId, float] = field(default_factory=dict)

    # Optional metrics for advanced scoring
    health_score: float = 1.0
    avg_latency_ms: float = 0.0
    active_requests: int = 0

    # Telemetry freshness
    telemetry_timestamp: float = 0.0  # When this snapshot was captured
    last_heartbeat: float = 0.0  # Last gateway heartbeat time

    # Cloud virtual catalogs have no GPU residency (backend_type == cloud_api).
    is_cloud: bool = False

    @property
    def telemetry_age_ms(self) -> int:
        """Age of telemetry in milliseconds."""
        if self.telemetry_timestamp == 0.0:
            return 0
        return int((time.time() - self.telemetry_timestamp) * 1000)

    def is_telemetry_stale(self, max_age_ms: int = 2000) -> bool:
        """True if telemetry is stale."""
        return self.telemetry_age_ms > max_age_ms

    def get_model_resource_usage(self, model_id: ModelId) -> tuple[int, int]:
        """
        Get (vram_mb, ram_mb) for a model, or (0, 0) if unknown.

        Args:
            model_id: ModelId object (not string)

        Note:
            Models in model_details are guaranteed to have valid requirements
            (collector.py excludes models with missing requirements).
            Returns (0, 0) only if model_id not in model_details.
        """
        details = self.model_details.get(model_id, {})
        if details:
            return (details.get("vram_usage", 0), details.get("ram_usage", 0))
        return (0, 0)

    @property
    def loaded_count(self) -> int:
        """Number of loaded models."""
        return len(self.loaded_models)


@dataclass(frozen=True, slots=True)
class Stargate:
    """
    Addressable inference endpoint (federated Remote Stargate).

    Post-unification: All Stargates are federated (no local Gateway).

    Invariant: ref is FederatedGateway
    """

    stargate_id: str
    ref: Any  # FederatedGateway only (post-unification)

    # Resource snapshot
    ram_free_mb: int
    vram_free_mb: int
    ram_total_mb: int = 0
    vram_total_mb: int = 0
    loaded_models: frozenset[ModelId] = field(default_factory=frozenset)
    busy_models: frozenset[ModelId] = field(default_factory=frozenset)
    loading_models: frozenset[ModelId] = field(default_factory=frozenset)
    available_models: frozenset[ModelId] = field(default_factory=frozenset)
    active_requests: int = 0

    # Telemetry freshness
    telemetry_timestamp: float = 0.0
    last_heartbeat: float = 0.0

    # Model details for eviction scoring
    model_details: dict[ModelId, ModelDetails] = field(default_factory=dict)

    @property
    def telemetry_age_ms(self) -> int:
        """Age of telemetry in milliseconds."""
        if self.telemetry_timestamp == 0.0:
            return 0
        return int((time.time() - self.telemetry_timestamp) * 1000)

    def is_telemetry_stale(self, max_age_ms: int = 2000) -> bool:
        """True if telemetry is stale."""
        return self.telemetry_age_ms > max_age_ms


@dataclass(frozen=True)
class SelectionResult:
    """Outcome of gateway selection."""

    gateway: Gateway | None
    reason: str
    slack_mb: int = 0
    priority: int = 0  # 1=loaded, 2=capacity, 3=eviction, 4=queue


# Function signatures for predicates and scorers
Predicate = Callable[[Gateway, Placement], bool]
Scorer = Callable[[Gateway, Placement], float]
