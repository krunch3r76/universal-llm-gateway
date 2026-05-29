"""Structural event-bus contract used by the tracker.

Defines the minimal ``publish_nowait`` surface the tracker depends on so the
emission machinery is not coupled to the concrete ``universal_event_bus``
implementation. See :func:`~.tracker_events._emit` for the async-wrapping note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from universal_event_bus import Event


class _EventBusProtocol(Protocol):
    """Minimal event-bus surface used by the tracker (avoids tight coupling).

    Note: ``publish_nowait`` is ``async def`` on the real event bus — the
    name is misleading. The tracker wraps the call in ``asyncio.create_task``
    so ``_emit`` can stay sync while the coroutine actually runs.
    """

    async def publish_nowait(self, event: Event) -> Any: ...
