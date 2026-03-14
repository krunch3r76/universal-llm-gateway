"""TUI lifecycle event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def TuiStarted(pid: int) -> Event:  # noqa: N802
    """Emitted when the TUI mounts and is ready."""
    return Event(signal="tui.started", payload={"pid": pid})


@event_factory
def TuiExited(reason: str) -> Event:  # noqa: N802
    """Emitted on clean TUI exit (q / ctrl+c / programmatic quit)."""
    return Event(signal="tui.exited", payload={"reason": reason})
