"""Activation authority-transition events for manage restart verification."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from universal_event_bus import Event, event_factory

logger = logging.getLogger(__name__)

_EVENTS_SOCK = os.environ.get(
    "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
)


@event_factory
def ManageRestartVerifying(  # noqa: N802
    intent_id: str,
    validation_id: str,
    service: str,
    kill_boundary_at: str,
    boundary_source: str,
) -> Event:
    """Kill returned; activation proof is now supervisor-owned."""
    return Event(
        signal="manage.restart.verifying",
        payload={
            "intent_id": intent_id,
            "validation_id": validation_id,
            "service": service,
            "kill_boundary_at": kill_boundary_at,
            "boundary_source": boundary_source,
        },
        role="observation",
        scope="node",
    )


@event_factory
def ManageRestartActivationValidated(  # noqa: N802
    intent_id: str,
    validation_id: str,
    code_ref_relation: str | None,
    identity_measurement: str | None,
) -> Event:
    """Validation CAS reached ``validated`` with identity evidence."""
    return Event(
        signal="manage.restart.activation.validated",
        payload={
            "intent_id": intent_id,
            "validation_id": validation_id,
            "code_ref_relation": code_ref_relation,
            "identity_measurement": identity_measurement,
        },
        role="observation",
        scope="node",
    )


@event_factory
def ManageRestartActivationUnverified(  # noqa: N802
    intent_id: str,
    validation_id: str,
    outcome: str,
    failure_reason: str | None,
    affordances: list[str] | None = None,
) -> Event:
    """Activation verify window closed without a validated outcome."""
    payload: dict[str, Any] = {
        "intent_id": intent_id,
        "validation_id": validation_id,
        "outcome": outcome,
        "failure_reason": failure_reason,
    }
    if affordances:
        payload["affordances"] = affordances
    return Event(
        signal="manage.restart.activation.unverified",
        payload=payload,
        role="observation",
        scope="node",
    )


@event_factory
def ManageRestartActivationProgress(  # noqa: N802
    intent_id: str,
    validation_id: str,
    progress_class: str,
    detail: dict[str, Any] | None = None,
) -> Event:
    """Observation-class change during activation verify (idle-clock reset)."""
    payload: dict[str, Any] = {
        "intent_id": intent_id,
        "validation_id": validation_id,
        "progress_class": progress_class,
    }
    if detail:
        payload["detail"] = detail
    return Event(
        signal="manage.restart.activation.progress",
        payload=payload,
        role="observation",
        scope="node",
    )


@event_factory
def ManagePropagationSettleFailed(  # noqa: N802
    service: str,
    validation_id: str | None,
    restart_intent: str | None,
    reason: str,
) -> Event:
    """Settle hook swallowed an exception after restart."""
    return Event(
        signal="manage.propagation.settle.failed",
        payload={
            "service": service,
            "validation_id": validation_id,
            "restart_intent": restart_intent,
            "reason": reason,
        },
        role="observation",
        scope="node",
    )


def publish_activation_event(event: Event) -> None:
    """Write one NDJSON ingest line for a factory Event. Silent on failure."""
    now = datetime.now(UTC)
    envelope: dict[str, Any] = {
        "signal": event.signal,
        "source": "manage",
        "role": event.role,
        "scope": event.scope,
        "timestamp": now.isoformat(),
        "ts_unix_ms": int(now.timestamp() * 1000),
        "payload": event.payload,
    }
    line = json.dumps(envelope, default=str) + "\n"
    try:
        with open(_EVENTS_SOCK, "wb") as sock:
            sock.write(line.encode())
    except Exception:
        logger.debug(
            "publish_activation_event failed for %s", event.signal, exc_info=True
        )


__all__ = [
    "ManagePropagationSettleFailed",
    "ManageRestartActivationProgress",
    "ManageRestartActivationUnverified",
    "ManageRestartActivationValidated",
    "ManageRestartVerifying",
    "publish_activation_event",
]
