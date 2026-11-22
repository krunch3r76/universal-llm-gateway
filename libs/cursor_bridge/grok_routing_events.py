"""Advisory events for grok window routing writes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_uds_publisher(publisher: Callable[[str, dict[str, Any]], None]) -> None:
    """Install a process-local publisher when ``mcp_events`` is unavailable."""
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


@event_factory
def GrokPreferredSpawnWritten(  # noqa: N802
    *,
    preferred_spawn: str,
    epoch: int,
    authority: str,
    projection: str,
    recovery: str,
    updated_by: str,
    source: str,
) -> Event:
    return Event(
        signal="grok.window.routing.preferred_spawn",
        role="observation",
        payload={
            "preferred_spawn": preferred_spawn,
            "epoch": epoch,
            "authority": authority,
            "projection": projection,
            "recovery": recovery,
            "updated_by": updated_by,
            "source": source,
        },
        scope="global",
    )


def emit_preferred_spawn_written(**kwargs: Any) -> None:
    """Emit after a successful ``write_preferred_spawn``."""
    _emit(GrokPreferredSpawnWritten(**kwargs))


__all__ = [
    "emit_preferred_spawn_written",
    "register_uds_publisher",
]