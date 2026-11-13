"""Process-wide single-flight guard for fleet operations (AC2)."""

from __future__ import annotations

import asyncio

_fleet_operation_guard = asyncio.Lock()
_fleet_operation_holder: str | None = None


class FleetOperationInProgressError(Exception):
    """Raised when a second fleet op is requested while one is still running."""

    def __init__(
        self, running_operation: str, *, requested_operation: str | None = None
    ) -> None:
        self.running_operation = running_operation
        self.requested_operation = requested_operation
        super().__init__(f"fleet op already running: {running_operation}")


async def claim_fleet_operation(operation: str) -> None:
    """Mark ``operation`` in flight; raise when another fleet op is already active."""
    global _fleet_operation_holder
    async with _fleet_operation_guard:
        if _fleet_operation_holder is not None:
            raise FleetOperationInProgressError(
                _fleet_operation_holder, requested_operation=operation
            )
        _fleet_operation_holder = operation


async def release_fleet_operation() -> None:
    global _fleet_operation_holder
    async with _fleet_operation_guard:
        _fleet_operation_holder = None
