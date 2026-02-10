"""
Single-gateway model load coordinator.

Coordinates concurrent model loading requests on a single gateway.
"""

import asyncio
from dataclasses import dataclass

from model_id import ModelId

from ..status import ModelLoadingStatus


class ModelLoadCoordinator:
    """
    Coordinates concurrent model loading requests on a SINGLE gateway.

    When multiple requests try to load the same model on the same gateway
    simultaneously, the first request becomes the coordinator and others
    wait for its result.

    Design Note: We intentionally do NOT cache errors. Only the coordinator
    request that initiated the load sees and propagates the actual error.
    Waiting requests receive FAILED status and can decide how to handle it.
    This prevents stale cached errors from persisting after Gateway clears
    the error state.
    """

    def __init__(self):
        self._loading_events: dict[tuple[str, str], asyncio.Event] = {}
        self._loading_results: dict[tuple[str, str], ModelLoadingStatus] = {}

    def create_load_event(self, gateway_name: str, model_id: ModelId) -> asyncio.Event:
        """
        Create and register a new loading event for coordinator.

        Safe for concurrent access: returns existing event if already registered.
        This prevents race conditions where a second caller overwrites the first
        caller's event, causing waiters to block indefinitely.
        """
        load_key = (gateway_name, model_id.routing_key)
        existing = self._loading_events.get(load_key)
        if existing:
            return existing
        event = asyncio.Event()
        self._loading_events[load_key] = event
        return event

    def get_load_event(
        self, gateway_name: str, model_id: ModelId
    ) -> asyncio.Event | None:
        """Get existing loading event if model is being loaded."""
        load_key = (gateway_name, model_id.routing_key)
        return self._loading_events.get(load_key)

    def store_result(
        self,
        gateway_name: str,
        model_id: ModelId,
        status: ModelLoadingStatus,
    ) -> None:
        """Store load result for waiters. Errors are NOT cached - only status."""
        load_key = (gateway_name, model_id.routing_key)
        self._loading_results[load_key] = status

    def get_result(self, gateway_name: str, model_id: ModelId) -> ModelLoadingStatus:
        """Get stored result for a completed load."""
        load_key = (gateway_name, model_id.routing_key)
        return self._loading_results.get(load_key, ModelLoadingStatus.FAILED)

    def cleanup(self, gateway_name: str, model_id: ModelId) -> None:
        """Clean up coordinator state for a completed load."""
        load_key = (gateway_name, model_id.routing_key)
        self._loading_events.pop(load_key, None)
        self._loading_results.pop(load_key, None)


@dataclass
class LoadCoordinationResult:
    """Result of a model load coordination request."""

    should_load: bool
    redirect_gateway: str | None = None
    wait_event: asyncio.Event | None = None
    error_message: str | None = None
