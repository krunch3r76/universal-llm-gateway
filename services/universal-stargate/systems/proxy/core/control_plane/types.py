"""
Type definitions and protocols for model orchestration.

Provides typed protocols for dependency injection and shared config helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from model_id import ModelId

if TYPE_CHECKING:
    from gateways import GatewayInstance
    from systems.routing.selection.types import Gateway

    from ..resource_manager import GatewayResourceManager


@runtime_checkable
class ResourceManagerProvider(Protocol):
    """Protocol for getting resource managers by gateway name."""

    def __call__(self, gateway_name: str) -> GatewayResourceManager | None:
        """Get resource manager for a specific gateway."""
        ...


@runtime_checkable
class SchedulerConfigProvider(Protocol):
    """Protocol for getting scheduler configuration."""

    def __call__(self) -> dict:
        """Get scheduler configuration dict."""
        ...


@dataclass(frozen=True, slots=True)
class ResourceRequirements:
    """Resource requirements for model loading."""

    vram_mb: int
    ram_mb: int


class MissingResourceRequirementsError(Exception):
    """Raised when resource requirements cannot be determined for a model."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(
            f"Cannot determine resource requirements for model '{model_id}'. "
            f"Model metadata must include vram_usage and ram_usage."
        )


@runtime_checkable
class ResourceRequirementsProvider(Protocol):
    """Protocol for fetching resource requirements from model metadata."""

    async def __call__(self, model_id: ModelId) -> ResourceRequirements:
        """
        Get resource requirements for a model.

        Args:
            model_id: ModelId object (parsed at API boundary)

        Returns:
            ResourceRequirements with vram_mb and ram_mb

        Raises:
            MissingResourceRequirementsError: If requirements cannot be determined
        """
        ...


# Protocol for immediate route attempt callback
# CHANGED: Return tuple for federated support
# Uses Protocol to match kw-only sticky parameter
@runtime_checkable
class AttemptImmediateRoute(Protocol):
    """Protocol for immediate route attempt callback."""

    async def __call__(
        self,
        model_id: ModelId,
        mock_request: dict[str, str],
        *,
        sticky: bool = True,
    ) -> tuple[GatewayInstance | None, Gateway | None]:
        """Attempt immediate routing for model."""
        ...


class ConfigHelper:
    """
    Centralized config access with caching.

    All config access should go through this helper to avoid
    repeated dict lookups and ensure consistent defaults.
    """

    # Queue-related defaults
    CHECK_INTERVAL: float = 2.0
    RESOURCE_RETRY_INTERVAL: float = 5.0
    MAX_UNLOAD_WAIT: int = 30

    def __init__(self, config_provider: SchedulerConfigProvider):
        self._get_config = config_provider
        self._cached_timeout: int | None = None

    @property
    def model_loading_timeout(self) -> int:
        """Get model loading timeout in seconds."""
        if self._cached_timeout is None:
            self._cached_timeout = self._get_config().get("model_loading_timeout", 300)
        return self._cached_timeout

    @property
    def check_interval(self) -> float:
        """Interval for polling immediate route availability."""
        return self.CHECK_INTERVAL

    @property
    def resource_retry_interval(self) -> float:
        """Interval between resource contention retries."""
        return self.RESOURCE_RETRY_INTERVAL

    @property
    def max_unload_wait(self) -> int:
        """Maximum time to wait for model unload in seconds."""
        return self.MAX_UNLOAD_WAIT

    def invalidate_cache(self) -> None:
        """Invalidate cached config values."""
        self._cached_timeout = None
