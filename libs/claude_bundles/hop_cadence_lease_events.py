"""Hop-cadence lease authority events — shared MCP + GIW emit path."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

_SOURCE = "giw.cursor_auto"


@event_factory
def GiwCursorAutoHopCadenceIdentityBound(  # noqa: N802
    thread_id: str,
    identity_source: str,
    watch_present: bool,
    registration_id: str | None,
) -> Event:
    """Caller identity resolved on a watched lane at request admission."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_identity_bound",
        payload={
            "thread_id": thread_id,
            "identity_source": identity_source,
            "watch_present": watch_present,
            "registration_id": registration_id,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceLeaseLost(  # noqa: N802
    thread_id: str,
    registration_id: str,
    identity_source: str,
    superseded_registration_id: str,
    successor_execution_id: str | None = None,
) -> Event:
    """Lease refused a superseded predecessor at agent_bus.request admission."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_lease_lost",
        payload={
            "thread_id": thread_id,
            "registration_id": registration_id,
            "identity_source": identity_source,
            "superseded_registration_id": superseded_registration_id,
            "successor_execution_id": successor_execution_id,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceFenceStarted(  # noqa: N802
    thread_id: str,
    superseded_registration_id: str,
    execution_id: str | None,
    satellite_execution_id: str | None,
) -> Event:
    """Joinable hop-claim armed a request fence on the lane."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_fence_started",
        payload={
            "thread_id": thread_id,
            "superseded_registration_id": superseded_registration_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceLeaseReclaimed(  # noqa: N802
    thread_id: str,
    superseded_registration_id: str,
    execution_id: str,
    action: str,
) -> Event:
    """CSE-terminal release cleared the request fence for a superseded seat."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_lease_reclaimed",
        payload={
            "thread_id": thread_id,
            "superseded_registration_id": superseded_registration_id,
            "execution_id": execution_id,
            "action": action,
        },
        scope="node",
        role="observation",
    )


def _mirror_to_event_service(event: Event) -> None:
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    payload = {
        "signal": event.signal,
        "source": _SOURCE,
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    with contextlib.suppress(Exception):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall((json.dumps(payload) + "\n").encode())


def emit_identity_bound(
    *,
    thread_id: str,
    identity_source: str,
    watch_present: bool,
    registration_id: str | None,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_identity_bound`` (best-effort)."""
    _mirror_to_event_service(
        GiwCursorAutoHopCadenceIdentityBound(
            thread_id=thread_id,
            identity_source=identity_source,
            watch_present=watch_present,
            registration_id=registration_id,
        )
    )


def emit_lease_lost(
    *,
    thread_id: str,
    registration_id: str,
    identity_source: str,
    superseded_registration_id: str,
    successor_execution_id: str | None = None,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_lease_lost`` (best-effort)."""
    _mirror_to_event_service(
        GiwCursorAutoHopCadenceLeaseLost(
            thread_id=thread_id,
            registration_id=registration_id,
            identity_source=identity_source,
            superseded_registration_id=superseded_registration_id,
            successor_execution_id=successor_execution_id,
        )
    )


def emit_fence_started(
    *,
    thread_id: str,
    superseded_registration_id: str,
    execution_id: str | None,
    satellite_execution_id: str | None,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_fence_started`` (best-effort)."""
    _mirror_to_event_service(
        GiwCursorAutoHopCadenceFenceStarted(
            thread_id=thread_id,
            superseded_registration_id=superseded_registration_id,
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
        )
    )


def emit_lease_reclaimed(
    *,
    thread_id: str,
    superseded_registration_id: str,
    execution_id: str,
    action: str,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_lease_reclaimed`` (best-effort)."""
    _mirror_to_event_service(
        GiwCursorAutoHopCadenceLeaseReclaimed(
            thread_id=thread_id,
            superseded_registration_id=superseded_registration_id,
            execution_id=execution_id,
            action=action,
        )
    )


__all__ = [
    "emit_fence_started",
    "emit_identity_bound",
    "emit_lease_lost",
    "emit_lease_reclaimed",
]
