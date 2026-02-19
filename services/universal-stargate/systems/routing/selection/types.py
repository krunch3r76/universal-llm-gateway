"""
Domain types for gateway selection.

Minimal type system: 3 dataclasses, 2 type aliases.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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

    @property
    def primary_resource_mb(self) -> int:
        """The constrained resource for this placement."""
        return self.vram_mb if self.is_gpu else self.ram_mb


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
    model_details: dict[ModelId, dict[str, Any]] = field(default_factory=dict)
    """
    Model details including last_inference_time and resource usage.

    Populated by collect_gateways():
    - When include_model_details=True: Full details from HTTP /api/v1/status/resources
    - When include_model_details=False: Minimal details from WebSocket cache
      (last_inference_time from MODEL_IDLE events, ram_usage=0, vram_usage=0)

    Used by eviction planning to calculate staleness scores.

    Structure: {
        model_id: {
            "last_inference_time": float | None,
            "vram_usage": int,  # MB (0 if from WebSocket cache)
            "ram_usage": int,   # MB (0 if from WebSocket cache)
            "status": str,
        }
    }
    """

    # Optional metrics for advanced scoring
    health_score: float = 1.0
    avg_latency_ms: float = 0.0
    active_requests: int = 0

    # Telemetry freshness
    telemetry_timestamp: float = 0.0  # When this snapshot was captured
    last_heartbeat: float = 0.0  # Last gateway heartbeat time

    @property
    def telemetry_age_ms(self) -> int:
        """Age of telemetry in milliseconds."""
        if self.telemetry_timestamp == 0.0:
            return 0
        return int((time.time() - self.telemetry_timestamp) * 1000)

    def is_telemetry_stale(self, max_age_ms: int = 2000) -> bool:
        """True if telemetry is stale."""
        return self.telemetry_age_ms > max_age_ms

    def get_last_inference_time(self, model_id: ModelId) -> float | None:
        """
        Get last inference time for a model, or None if unknown.

        Args:
            model_id: ModelId object (not string)
        """
        if model_id in self.model_details:
            return self.model_details[model_id].get("last_inference_time")
        return None

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
        if model_id in self.model_details:
            details = self.model_details[model_id]
            # No "or 0" needed - collector guarantees valid requirements
            return (
                details.get("vram_usage", 0),
                details.get("ram_usage", 0),
            )
        return (0, 0)

    def slack_after_fit(self, p: Placement) -> int:
        """Remaining primary resource after placing model."""
        if p.is_gpu:
            return self.vram_free_mb - p.vram_mb
        return self.ram_free_mb - p.ram_mb

    def has_model_loaded(self, model_id: ModelId) -> bool:
        """Check if model is loaded on this gateway."""
        return model_id in self.loaded_models

    def is_model_idle(self, model_id: ModelId) -> bool:
        """Check if model is loaded and not busy."""
        return model_id in self.loaded_models and model_id not in self.busy_models

    def is_model_loading(self, model_id: ModelId) -> bool:
        """Check if model is currently being loaded."""
        return model_id in self.loading_models

    def is_model_busy_or_loading(self, model_id: ModelId) -> bool:
        """Check if model is busy (in use or loading)."""
        return model_id in self.busy_models or model_id in self.loading_models

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
    model_details: dict[ModelId, dict[str, Any]] = field(default_factory=dict)

    @property
    def telemetry_age_ms(self) -> int:
        """Age of telemetry in milliseconds."""
        if self.telemetry_timestamp == 0.0:
            return 0
        return int((time.time() - self.telemetry_timestamp) * 1000)

    def is_telemetry_stale(self, max_age_ms: int = 2000) -> bool:
        """True if telemetry is stale."""
        return self.telemetry_age_ms > max_age_ms

    def has_model_loaded(self, model_id: ModelId) -> bool:
        """Check if model is loaded on this stargate."""
        return model_id in self.loaded_models

    def is_model_idle(self, model_id: ModelId) -> bool:
        """Check if model is loaded and not busy."""
        return model_id in self.loaded_models and model_id not in self.busy_models

    def slack_after_fit(self, p: Placement) -> int:
        """Remaining primary resource after placing model."""
        if p.is_gpu:
            return self.vram_free_mb - p.vram_mb
        return self.ram_free_mb - p.ram_mb


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
