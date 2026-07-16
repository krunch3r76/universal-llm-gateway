"""Endeavor birth gate event emitters (F-B6)."""

from __future__ import annotations

from typing import Any

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from ..event_publisher import record


@event_factory
def cortex_endeavor_birth_incomplete(
    host: str,
    missing: list[str],
    resume_blocking: bool,
    ack: bool,
) -> Event:
    ev = Event(
        signal="cortex.endeavor.birth.incomplete",
        role="observation",
        scope="global",
        payload={
            "host": host,
            "missing": missing,
            "resume_blocking": resume_blocking,
            "ack": ack,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_birth_ack(host: str, reason: str) -> Event:
    ev = Event(
        signal="cortex.endeavor.birth.ack",
        role="observation",
        scope="global",
        payload={"host": host, "reason": reason},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_strategy_pin_missing(host: str, row_id: str) -> Event:
    ev = Event(
        signal="cortex.endeavor.strategy.pin.missing",
        role="observation",
        scope="global",
        payload={"host": host, "row_id": row_id},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_row_pending(host: str, row_id: str, pin: int) -> Event:
    ev = Event(
        signal="cortex.endeavor.row.pending",
        role="observation",
        scope="global",
        payload={"host": host, "row_id": row_id, "pin": pin},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_row_disposed(host: str, row_id: str, pin: int) -> Event:
    ev = Event(
        signal="cortex.endeavor.row.disposed",
        role="observation",
        scope="global",
        payload={"host": host, "row_id": row_id, "pin": pin},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_lock_blocked(
    host: str,
    deliverable: str,
    pending_rows: list[dict[str, Any]],
) -> Event:
    ev = Event(
        signal="cortex.endeavor.lock.blocked",
        role="observation",
        scope="global",
        payload={
            "host": host,
            "deliverable": deliverable,
            "pending_rows": pending_rows,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_audit_finding(
    host: str,
    tier: str,
    missing: list[str],
    resume_blocking: bool,
) -> Event:
    ev = Event(
        signal="cortex.endeavor.audit.finding",
        role="observation",
        scope="global",
        payload={
            "host": host,
            "tier": tier,
            "missing": missing,
            "resume_blocking": resume_blocking,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def cortex_endeavor_repaired(
    tier: str,
    repaired: int,
    residual: int,
    applied: bool,
) -> Event:
    ev = Event(
        signal="cortex.endeavor.repaired",
        role="observation",
        scope="global",
        payload={
            "tier": tier,
            "repaired": repaired,
            "residual": residual,
            "applied": applied,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
