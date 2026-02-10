"""
Wait handles and result types for model loading/unloading.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class _GatewayWsClient(Protocol):
    """Protocol for gateway WebSocket client."""

    @property
    def is_connected(self) -> bool: ...

    def get_loaded_models(self) -> frozenset[str]: ...


class LoadResult(StrEnum):
    """Result of awaiting model load."""

    LOADED = "loaded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    GATEWAY_UNREACHABLE = "gateway_unreachable"


class UnloadResult(StrEnum):
    """Result of awaiting model unload."""

    UNLOADED = "unloaded"
    TIMEOUT = "timeout"
    GATEWAY_UNREACHABLE = "gateway_unreachable"


@dataclass
class LoadWaitHandle:
    """Handle for a single load wait operation."""

    gateway_name: str
    model_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: LoadResult = LoadResult.TIMEOUT
    error_message: str | None = None
    waiter_count: int = 0

    def set_loaded(self) -> None:
        self.result = LoadResult.LOADED
        self.event.set()

    def set_failed(self, error: str | None = None) -> None:
        self.result = LoadResult.FAILED
        self.error_message = error
        self.event.set()

    def set_unreachable(self) -> None:
        self.result = LoadResult.GATEWAY_UNREACHABLE
        self.event.set()


@dataclass
class UnloadWaitHandle:
    """Handle for a single unload wait operation."""

    gateway_name: str
    model_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: UnloadResult = UnloadResult.TIMEOUT
    waiter_count: int = 0

    def set_unloaded(self) -> None:
        self.result = UnloadResult.UNLOADED
        self.event.set()

    def set_unreachable(self) -> None:
        self.result = UnloadResult.GATEWAY_UNREACHABLE
        self.event.set()
