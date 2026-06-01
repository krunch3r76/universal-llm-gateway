"""
Expiring item pattern for event-driven cleanup.

Each item manages its own expiration task instead of periodic scanning.
This eliminates cleanup loops in favor of per-item asyncio.Task expiration.

Architecture:
    # OLD (periodic scanning):
    Component
      └── _cleanup_task = create_task(_cleanup_loop())
            └── while True:
                  └── for item in self.items:
                        if item.is_expired():
                            cleanup(item)
                  └── await asyncio.sleep(interval)

    # NEW (per-item expiration):
    Component
      └── create_item()
            └── item._expiration_task = create_task(sleep(ttl) → cleanup(item))
      └── release_item()
            └── item._expiration_task.cancel()
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from universal_logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class ExpiringItem[T]:
    """
    Wraps an item with self-managing expiration.

    Instead of periodic cleanup loops, each item has its own
    expiration task that fires after TTL.

    Usage:
        item = ExpiringItem(
            value=my_data,
            ttl=300.0,
            on_expire=lambda item: cleanup(item.key),
            key="item-123",
        )
        item.schedule_expiration()

        # On early release:
        item.cancel_expiration()
    """

    value: T
    ttl: float
    on_expire: Callable[[ExpiringItem[T]], None]
    key: str = ""

    _expiration_task: asyncio.Task | None = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)

    def schedule_expiration(self) -> None:
        """Schedule the expiration task. Call after creation."""
        if self._expiration_task is not None:
            return  # Already scheduled

        self._expiration_task = asyncio.create_task(
            self._expire_after_ttl(),
            name=f"expire-{self.key}",
        )

    async def _expire_after_ttl(self) -> None:
        """Wait for TTL then call expiration callback."""
        try:
            await asyncio.sleep(self.ttl)

            if not self._cancelled:
                logger.debug(f"Item {self.key} expired after {self.ttl}s")
                self.on_expire(self)

        except asyncio.CancelledError:
            # Expected when cancelled early
            pass

    def cancel_expiration(self) -> None:
        """Cancel the expiration task (e.g., on early release)."""
        self._cancelled = True
        if self._expiration_task and not self._expiration_task.done():
            self._expiration_task.cancel()
            self._expiration_task = None

    def refresh_ttl(self, new_ttl: float | None = None) -> None:
        """
        Reset the expiration timer.

        Useful for items that should stay alive on activity.
        """
        if new_ttl is not None:
            self.ttl = new_ttl

        # Cancel old task
        if self._expiration_task and not self._expiration_task.done():
            self._expiration_task.cancel()

        # Schedule new task
        self._cancelled = False
        self._expiration_task = asyncio.create_task(
            self._expire_after_ttl(),
            name=f"expire-{self.key}",
        )


class ExpiringRegistry[T]:
    """
    Registry of expiring items with automatic cleanup.

    No background loop - each item manages its own expiration.

    Usage:
        registry = ExpiringRegistry[ClientInfo](default_ttl=3600.0)
        registry.add("client-123", client_info)

        # On activity:
        registry.refresh("client-123")

        # On explicit removal:
        registry.remove("client-123")
    """

    def __init__(self, default_ttl: float):
        self._items: dict[str, ExpiringItem[T]] = {}
        self._default_ttl = default_ttl

    def add(
        self,
        key: str,
        value: T,
        ttl: float | None = None,
    ) -> ExpiringItem[T]:
        """Add an item with expiration."""
        # Remove existing if present
        if key in self._items:
            self.remove(key)

        item = ExpiringItem(
            value=value,
            ttl=ttl or self._default_ttl,
            on_expire=self._on_item_expired,
            key=key,
        )

        self._items[key] = item
        item.schedule_expiration()

        return item

    def _on_item_expired(self, item: ExpiringItem[T]) -> None:
        """Called when an item expires."""
        if item.key in self._items:
            del self._items[item.key]
            logger.debug(f"Expired and removed: {item.key}")

    def get(self, key: str) -> T | None:
        """Get item value, or None if not present/expired."""
        item = self._items.get(key)
        return item.value if item else None

    def refresh(self, key: str, new_ttl: float | None = None) -> bool:
        """
        Refresh an item's TTL (e.g., on activity).

        Returns True if item exists, False otherwise.
        """
        item = self._items.get(key)
        if item:
            item.refresh_ttl(new_ttl)
            return True
        return False

    def remove(self, key: str) -> T | None:
        """
        Explicitly remove an item (cancels expiration).

        Returns the value if present, None otherwise.
        """
        item = self._items.pop(key, None)
        if item:
            item.cancel_expiration()
            return item.value
        return None

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def keys(self):
        return self._items.keys()

    def values(self):
        return (item.value for item in self._items.values())

    def clear(self) -> None:
        """Clear all items, cancelling their expiration tasks."""
        for item in self._items.values():
            item.cancel_expiration()
        self._items.clear()
