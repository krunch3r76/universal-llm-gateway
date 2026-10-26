"""Observation events for MCP restart gate (check, timeout, snapshot restore/fail)."""

from __future__ import annotations

from typing import Any

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def mcp_restart_check_failed(*, detail: str) -> Event:
    return Event(
        signal="mcp.restart.check.failed",
        role="observation",
        scope="node",
        payload={"detail": detail[:500]},
    )


@event_factory
def mcp_restart_check_timeout(*, detail: str) -> Event:
    return Event(
        signal="mcp.restart.check.timeout",
        role="observation",
        scope="node",
        payload={"detail": detail[:500]},
    )


@event_factory
def mcp_restart_snapshot_restored(*, reason: str) -> Event:
    return Event(
        signal="mcp.restart.snapshot.restored",
        role="observation",
        scope="node",
        payload={"reason": reason},
    )


@event_factory
def mcp_restart_log(*, detail: str) -> Event:
    return Event(
        signal="mcp.restart.log",
        role="observation",
        scope="node",
        payload={"detail": detail[:500]},
    )


@event_factory
def mcp_restart_snapshot_failed(*, reason: str) -> Event:
    return Event(
        signal="mcp.restart.snapshot.failed",
        role="observation",
        scope="node",
        payload={"reason": reason},
    )


def emit_restart_event(signal: str, payload: dict[str, Any]) -> None:
    """Best-effort publish when manage observation socket is available."""
    factories = {
        "mcp.restart.check.failed": lambda: mcp_restart_check_failed(
            detail=str(payload.get("detail", ""))
        ),
        "mcp.restart.check.timeout": lambda: mcp_restart_check_timeout(
            detail=str(payload.get("detail", ""))
        ),
        "mcp.restart.snapshot.restored": lambda: mcp_restart_snapshot_restored(
            reason=str(payload.get("reason", ""))
        ),
        "mcp.restart.snapshot.failed": lambda: mcp_restart_snapshot_failed(
            reason=str(payload.get("reason", ""))
        ),
        "mcp.restart.log": lambda: mcp_restart_log(
            detail=str(payload.get("detail", payload.get("message", "")))
        ),
    }
    factory = factories.get(signal)
    if factory is None:
        return
    event = factory()
    try:
        from scripts.model_manager.observation_event import _emit_sync

        _emit_sync(
            event.signal,
            dict(event.payload),
            source="manage",
            role=event.role,
            scope=event.scope,
        )
    except Exception:
        pass


__all__ = [
    "emit_restart_event",
    "mcp_restart_check_failed",
    "mcp_restart_check_timeout",
    "mcp_restart_log",
    "mcp_restart_snapshot_failed",
    "mcp_restart_snapshot_restored",
]
