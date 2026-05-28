"""Minimal event-bus surface used by the delivery package.

Defined as a Protocol (structural typing) so any object exposing an async
``publish_nowait(event)`` method satisfies the contract — the delivery code
does not depend on the concrete ``universal_event_bus`` implementation.

The Protocol stub parameter is underscore-prefixed (``_event``) so that
vulture does not flag it as an unused parameter in the ``...``-body method.
The name carries no API significance — call sites in ``_emit`` invoke
``bus.publish_nowait(event)`` positionally, not by keyword.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from universal_event_bus import Event


class _EventBusProtocol(Protocol):
    """Minimal event-bus surface used by the delivery module."""

    async def publish_nowait(self, _event: Event) -> Any: ...
