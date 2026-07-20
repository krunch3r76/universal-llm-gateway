"""EventBus subscriber that persists matching signals into AsyncEventStore."""

from typing import Any

from universal_logging import get_logger

from .store import AsyncEventStore

logger = get_logger(__name__)


class AsyncEventStoreSubscriber:
    """Subscriber that automatically stores events in AsyncEventStore."""

    def __init__(
        self,
        event_bus,
        event_store: AsyncEventStore,
        event_signals: list[str] | None = None,
    ):
        self.event_bus = event_bus
        self.event_store = event_store
        self.event_signals = event_signals

        if event_signals:
            for signal in event_signals:
                event_bus.subscribe_async(signal, self._handle_event)
                logger.debug(
                    f"AsyncEventStoreSubscriber: Subscribed to signal '{signal}'"
                )
        else:
            logger.warning(
                "AsyncEventStoreSubscriber: No event_signals specified, "
                "manual subscription required"
            )

    async def _handle_event(self, event: Any):
        await self.event_store.store_event(event)
