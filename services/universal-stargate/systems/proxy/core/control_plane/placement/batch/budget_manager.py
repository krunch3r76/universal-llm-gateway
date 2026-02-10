"""
Dynamic budget management with event-driven cache invalidation.

Tracks gateway resource budgets (VRAM/RAM) with lazy refresh.
Cache invalidated on model.loaded/model.unloaded events.

Domain: Proxy
Pattern: Event-driven cache invalidation (per architecture_ws.mdc)

Event Integration:
- Subscribe to model.loaded, model.unloaded events
- Invalidate cache on event (no polling)
- Lazy refresh on next get_budgets() call
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import SingleGatewayManager

logger = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class GatewayBudget:
    """Immutable snapshot of gateway resource budget."""

    gateway_name: str
    vram_free_mb: int
    ram_free_mb: int
    loaded_model_count: int


class DynamicBudgetManager:
    """
    Track gateway budgets dynamically with event-driven invalidation.

    Event-driven cache invalidation per architecture_ws.mdc:
    - Invalidate on model.loaded/model.unloaded events
    - Lazy refresh on next get_budgets() call
    - No polling (event-driven waiting)

    Cache TTL provides defensive fallback for missed events.

    Usage:
        from src.scheduling.events import MODEL_LOADED, MODEL_UNLOADED

        manager = DynamicBudgetManager(gateway_manager, cache_ttl=60)

        # Event subscription (done in integration layer)
        event_bus.subscribe_async(MODEL_LOADED, lambda e: manager.invalidate())
        event_bus.subscribe_async(MODEL_UNLOADED, lambda e: manager.invalidate())

        # Get current budgets (lazy refresh if invalidated)
        budgets = await manager.get_budgets()
    """

    def __init__(
        self,
        gateway_manager: SingleGatewayManager,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        """
        Initialize budget manager.

        Args:
            gateway_manager: Gateway manager for budget queries
            cache_ttl_seconds: Max cache age before forced refresh (defensive)
        """
        self._gateway_manager = gateway_manager
        self._cache_ttl = cache_ttl_seconds

        # Cache state
        self._cached_budgets: dict[str, GatewayBudget] = {}
        self._cache_valid = False
        self._cache_timestamp: float = 0.0

    async def get_budgets(self) -> dict[str, GatewayBudget]:
        """
        Get current gateway budgets, refreshing if invalidated.

        Returns:
            Dict mapping gateway_name → GatewayBudget
        """
        if not self._is_cache_valid():
            await self._refresh_budgets()

        return self._cached_budgets.copy()

    async def get_budget(self, gateway_name: str) -> GatewayBudget | None:
        """
        Get budget for specific gateway.

        Args:
            gateway_name: Gateway to query

        Returns:
            GatewayBudget if gateway exists, None otherwise
        """
        budgets = await self.get_budgets()
        return budgets.get(gateway_name)

    async def get_total_free_resources(self) -> tuple[int, int]:
        """
        Get total free resources across all gateways.

        Returns:
            Tuple of (total_vram_free_mb, total_ram_free_mb)
        """
        budgets = await self.get_budgets()
        total_vram = sum(b.vram_free_mb for b in budgets.values())
        total_ram = sum(b.ram_free_mb for b in budgets.values())
        return total_vram, total_ram

    def invalidate(self) -> None:
        """
        Mark cache invalid (call on model.loaded/model.unloaded events).

        Event subscription pattern per architecture_ws.mdc:
        - Components subscribe and react to events
        - No manual updates scattered across files
        """
        self._cache_valid = False
        logger.debug("Budget cache invalidated")

    def force_refresh(self) -> None:
        """
        Force cache refresh on next get_budgets() call.

        Use when you know state has changed but events may be delayed.
        """
        self._cache_valid = False
        self._cache_timestamp = 0.0

    def _is_cache_valid(self) -> bool:
        """Check if cache is valid (not invalidated and not expired)."""
        if not self._cache_valid:
            return False

        # Defensive TTL check (events should normally keep us fresh)
        age = time.time() - self._cache_timestamp
        if age > self._cache_ttl:
            logger.debug(
                f"Budget cache expired (age: {age:.1f}s > TTL: {self._cache_ttl}s)"
            )
            return False

        return True

    async def _refresh_budgets(self) -> None:
        """Refresh budgets from gateway manager."""
        from systems.routing.selection.collector import collect_gateways

        try:
            gateway = self._gateway_manager.get_gateway()
            if not gateway:
                self._cached_budgets = {}
                self._cache_valid = True
                self._cache_timestamp = time.time()
                return

            gateways = await collect_gateways(
                [gateway],
                include_model_details=True,
                gateway_manager=self._gateway_manager,
            )

            self._cached_budgets = {
                g.name: GatewayBudget(
                    gateway_name=g.name,
                    vram_free_mb=g.vram_free_mb,
                    ram_free_mb=g.ram_free_mb,
                    loaded_model_count=len(g.loaded_models),
                )
                for g in gateways
            }
            self._cache_valid = True
            self._cache_timestamp = time.time()

            gw_count = len(self._cached_budgets)
            gw_summary = ", ".join(
                f"{b.gateway_name}={b.vram_free_mb}MB"
                for b in self._cached_budgets.values()
            )
            logger.debug(f"Budget refresh [{gw_count} gw]: {gw_summary}")

        except Exception as e:
            logger.error(f"Failed to refresh budgets: {e}", exc_info=True)
            raise  # Fail fast - let caller decide retry strategy

    @property
    def cache_age_seconds(self) -> float:
        """Get age of cache in seconds."""
        if self._cache_timestamp == 0.0:
            return float("inf")
        return time.time() - self._cache_timestamp
