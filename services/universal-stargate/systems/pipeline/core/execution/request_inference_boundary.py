"""Request-scoped inference boundary subscriptions for pipeline execution."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from universal_event_bus import EventBus, Subscription

logger = logging.getLogger(__name__)


class EventPayloadProtocol(Protocol):
    """Minimal event shape required for request boundary correlation."""

    payload: dict[str, Any]


@dataclass(slots=True, kw_only=True)
class BoundaryObservation:
    """One observed request-boundary event with its payload and arrival time."""

    signal: str
    observed_at_monotonic: float
    payload: dict[str, Any]


@dataclass(slots=True, kw_only=True)
class RequestInferenceBoundaryState:
    """Tracked request-boundary state for one in-flight pipeline request."""

    request_id: str
    inference_started: BoundaryObservation | None = None
    fallback_processing: BoundaryObservation | None = None


class RequestInferenceBoundaryTracker:
    """Subscribe to request-scoped inference boundary signals for pipelines.

    Primary boundary:
    - ``request.inference.started``: downstream runtime confirmed execution start

    Fallback boundary:
    - ``request.processing``: request admitted for processing when primary signal
      is unavailable
    """

    def __init__(
        self,
        *,
        states: dict[str, RequestInferenceBoundaryState],
        subscriptions: list[Subscription],
    ) -> None:
        self._states = states
        self._subscriptions = subscriptions

    @property
    def states(self) -> dict[str, RequestInferenceBoundaryState]:
        """Return tracked state by request_id."""
        return self._states

    @classmethod
    def subscribe(
        cls,
        *,
        event_bus: EventBus | None,
        request_ids: Iterable[str],
        on_inference_started: (
            Callable[[str, RequestInferenceBoundaryState], None] | None
        ) = None,
        on_processing: (
            Callable[[str, RequestInferenceBoundaryState], None] | None
        ) = None,
    ) -> RequestInferenceBoundaryTracker:
        """Create subscriptions for request-scoped inference boundary events."""
        states = {
            request_id: RequestInferenceBoundaryState(request_id=request_id)
            for request_id in request_ids
        }
        if not event_bus or not states:
            return cls(states=states, subscriptions=[])

        subscriptions: list[Subscription] = []

        def _record_primary(event: EventPayloadProtocol) -> None:
            request_id = event.payload.get("request_id")
            if not isinstance(request_id, str):
                return
            state = states.get(request_id)
            if state is None or state.inference_started is not None:
                return
            state.inference_started = BoundaryObservation(
                signal="request.inference.started",
                observed_at_monotonic=time.monotonic(),
                payload=dict(event.payload),
            )
            if on_inference_started is not None:
                on_inference_started(request_id, state)

        def _record_fallback(event: EventPayloadProtocol) -> None:
            request_id = event.payload.get("request_id")
            if not isinstance(request_id, str):
                return
            state = states.get(request_id)
            if state is None or state.fallback_processing is not None:
                return
            state.fallback_processing = BoundaryObservation(
                signal="request.processing",
                observed_at_monotonic=time.monotonic(),
                payload=dict(event.payload),
            )
            if on_processing is not None:
                on_processing(request_id, state)

        async def _on_request_inference_started(event: EventPayloadProtocol) -> None:
            _record_primary(event)

        async def _on_request_processing(event: EventPayloadProtocol) -> None:
            _record_fallback(event)

        subscriptions.append(
            event_bus.subscribe_async(
                "request.inference.started",
                _on_request_inference_started,
            )
        )
        subscriptions.append(
            event_bus.subscribe_async("request.processing", _on_request_processing)
        )
        return cls(states=states, subscriptions=subscriptions)

    def close(self) -> None:
        """Unsubscribe all tracked handlers."""
        for subscription in self._subscriptions:
            subscription.unsubscribe()
