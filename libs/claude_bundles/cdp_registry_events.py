"""CDP registry observation events — @event_factory + best-effort UDS ingest."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from typing import TYPE_CHECKING

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

if TYPE_CHECKING:
    from claude_bundles.cdp_registry import Registration


@event_factory
def cdp_port_registered(reg: Registration) -> Event:
    return Event(
        signal="cdp.port.registered",
        role="observation",
        scope="node",
        payload=_payload(reg),
    )


@event_factory
def cdp_port_deregistered(reg: Registration) -> Event:
    return Event(
        signal="cdp.port.deregistered",
        role="observation",
        scope="node",
        payload=_payload(reg),
    )


@event_factory
def cdp_port_reattached(reg: Registration) -> Event:
    return Event(
        signal="cdp.port.reattached",
        role="observation",
        scope="node",
        payload=_payload(reg),
    )


@event_factory
def cdp_port_exit_kill_decision(
    *,
    purpose: str | None,
    registration_id: str,
    port: int,
    kill: bool,
) -> Event:
    return Event(
        signal="cdp.port.exit_kill_decision",
        role="observation",
        scope="node",
        payload={
            "purpose": purpose,
            "registration_id": registration_id,
            "port": port,
            "kill": kill,
        },
    )


@event_factory
def cdp_port_orphaned_alive_reaped(
    *,
    registration_id: str,
    port: int,
    trigger: str,
    reaped_orphaned_alive: str | None,
) -> Event:
    return Event(
        signal="cdp.port.orphaned_alive_reaped",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "port": port,
            "trigger": trigger,
            "reaped_orphaned_alive": reaped_orphaned_alive,
        },
    )


@event_factory
def cdp_port_orphan_scan(
    *,
    ports_live: int,
    ports_skipped_registered: int,
    ports_examined: int,
    matched_count: int,
    rejected_count: int,
    unevaluable_count: int,
    closable_count: int = 0,
    protected_count: int = 0,
) -> Event:
    return Event(
        signal="cdp.port.orphan_scan",
        role="observation",
        scope="node",
        payload={
            "ports_live": ports_live,
            "ports_skipped_registered": ports_skipped_registered,
            "ports_examined": ports_examined,
            "matched_count": matched_count,
            "rejected_count": rejected_count,
            "unevaluable_count": unevaluable_count,
            "closable_count": closable_count,
            "protected_count": protected_count,
            "reclaim_enabled": False,
        },
    )


@event_factory
def cdp_protocol_parked(
    *,
    cse_id: str,
    registration_id: str | None,
    thread: str,
    turn_id: int,
    obligation_id: str,
    wake_channel: str,
    fallback: str,
) -> Event:
    """Transition-class: PARKED mirror opens wake_owed obligation."""
    return Event(
        signal="cdp.protocol.parked",
        role="coordination",
        scope="node",
        payload={
            "cse_id": cse_id,
            "registration_id": registration_id,
            "thread": thread,
            "turn_id": turn_id,
            "obligation_id": obligation_id,
            "wake_channel": wake_channel,
            "fallback": fallback,
        },
    )


@event_factory
def cdp_wake_delivered(
    *,
    cse_id: str,
    registration_id: str | None,
    thread: str | None,
    obligation_id: str,
    send_verified: bool,
) -> Event:
    """Transition-class: followup success discharges wake_owed."""
    return Event(
        signal="cdp.wake.delivered",
        role="coordination",
        scope="node",
        payload={
            "cse_id": cse_id,
            "registration_id": registration_id,
            "thread": thread,
            "obligation_id": obligation_id,
            "send_verified": send_verified,
        },
    )


@event_factory
def cdp_wake_alarm_fired(
    *,
    cse_id: str,
    registration_id: str | None,
    thread: str | None,
    obligation_id: str,
    fallback: str,
    outcome_code: str,
) -> Event:
    """Transition-class: TTL alarm on unpaid wake_owed."""
    return Event(
        signal="cdp.wake.alarm_fired",
        role="coordination",
        scope="node",
        payload={
            "cse_id": cse_id,
            "registration_id": registration_id,
            "thread": thread,
            "obligation_id": obligation_id,
            "fallback": fallback,
            "outcome_code": outcome_code,
        },
    )


@event_factory
def cdp_seat_superseded(
    *,
    outgoing_registration_id: str,
    successor_registration_id: str | None,
    reason: str,
) -> Event:
    """Rebind closed open wake_owed / stop_ack_owed on the outgoing registration."""
    return Event(
        signal="cdp.seat.superseded",
        role="coordination",
        scope="node",
        payload={
            "outgoing_registration_id": outgoing_registration_id,
            "successor_registration_id": successor_registration_id,
            "reason": reason,
            "registration_id": outgoing_registration_id,
        },
    )


@event_factory
def cdp_occupancy_overlap(*, lane: str, execution_ids: list[str]) -> Event:
    """Census OVERLAP: ≥2 operator-purpose streams on one recorded lane."""
    return Event(
        signal="cdp.occupancy.overlap",
        role="observation",
        scope="node",
        payload={"lane": lane, "execution_ids": execution_ids},
    )


def _payload(reg: Registration) -> dict:
    return {
        "registration_id": reg.registration_id,
        "port": reg.port,
        "profile_suffix": reg.profile_suffix,
        "holder": reg.holder,
        "purpose": reg.purpose,
        "mission_kind": reg.mission_kind,
        "parent_thread": reg.parent_thread,
    }


def emit(event: Event) -> None:
    """Best-effort UDS ingest — never raises."""
    _mirror_to_event_service(event)


def emit_transition(event: Event, *, transition_record: dict) -> None:
    """ACK'd durable transition: fsync log + fold projection, then best-effort mirror.

    Raises ``RegistryStoreError`` when local durability fails.
    """
    from claude_bundles.cse_session_fold import append_session_transition_locked

    append_session_transition_locked(transition_record, event=event)


def _mirror_to_event_service(event: Event) -> None:
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    payload = {
        "signal": event.signal,
        "source": "cdp-registry",
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
