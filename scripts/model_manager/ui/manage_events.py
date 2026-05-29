"""Manage API event factories for service lifecycle observation signals."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def ManageServiceRequested(method: str, service: str) -> Event:  # noqa: N802
    """Emitted when a manage API request is received, before execution begins."""
    return Event(
        signal="manage.service.requested",
        payload={"method": method, "service": service},
    )


@event_factory
def ManageServiceCompleted(method: str, service: str, duration_s: float) -> Event:  # noqa: N802
    """Emitted when a manage API operation completes successfully."""
    return Event(
        signal="manage.service.completed",
        payload={"method": method, "service": service, "duration_s": duration_s},
    )


@event_factory
def ManageServiceFailed(  # noqa: N802
    method: str, service: str, error: str, duration_s: float
) -> Event:
    """Emitted when a manage API operation fails with an error message."""
    return Event(
        signal="manage.service.failed",
        payload={
            "method": method,
            "service": service,
            "error": error,
            "duration_s": duration_s,
        },
    )


@event_factory
def ManageRestartDeferred(  # noqa: N802
    method: str, service: str, state: str, reason: str, retry_after_s: int
) -> Event:
    """Emitted when a stop/restart/sync_restart is deferred by the drain gate.

    state ∈ {"busy", "in_progress", "probe_error"}.
    """
    return Event(
        signal="manage.restart.deferred",
        payload={
            "method": method,
            "service": service,
            "state": state,
            "reason": reason,
            "retry_after_s": retry_after_s,
        },
    )
