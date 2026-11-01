"""webtab.* observation events (eyes slice)."""

from __future__ import annotations

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory
from web_chat_relay.events import emit as _emit_ingest


@event_factory
def webtab_probe_completed(
    *,
    house_id: str,
    target_id: str,
    tab_count: int,
) -> Event:
    """Read-only probe finished (no navigation side effects)."""
    return Event(
        signal="webtab.probe.completed",
        role="observation",
        scope="node",
        payload={
            "house_id": house_id,
            "target_id": target_id,
            "tab_count": tab_count,
        },
    )


def emit_probe_completed(*, house_id: str, target_id: str, tab_count: int) -> None:
    """Best-effort bus emit; never raises into probe callers."""
    event = webtab_probe_completed(
        house_id=house_id,
        target_id=target_id,
        tab_count=tab_count,
    )
    _emit_ingest(event)
