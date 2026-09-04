"""Conductor row-hop observation events (todo:conductor-hop-reactor R1).

Separate from ``cursor_sdk_events`` so hop lifecycle stays single-responsibility.
Emitters wire in R3 (reactor); R1 lands vocabulary + catalog only.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

_HOP_REASONS = frozenset({"spawn", "planned", "crash", "silent", "watchdog", "park_harvest"})


@event_factory
def FrontierSdkConductorHopDeclared(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    hop_reason: str,
    row_closed: str | None = None,
) -> Event:
    """Closeout carried ROW_HOP before terminal transition."""
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "hop_seq": hop_seq,
        "hop_reason": hop_reason,
    }
    if row_closed is not None:
        payload["row_closed"] = row_closed
    return Event(
        signal="frontier.sdk.conductor.hop.declared",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkConductorHopAdmitted(  # noqa: N802
    predecessor_dispatch_id: str,
    successor_dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    hop_reason: str,
) -> Event:
    """Substrate admitted a conductor hop successor after predecessor terminal."""
    return Event(
        signal="frontier.sdk.conductor.hop.admitted",
        payload={
            "predecessor_dispatch_id": predecessor_dispatch_id,
            "successor_dispatch_id": successor_dispatch_id,
            "thread_id": thread_id,
            "hop_seq": hop_seq,
            "hop_reason": hop_reason,
        },
        scope="node",
    )


@event_factory
def FrontierSdkConductorHopAdmitFailed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    hop_reason: str,
    error: str,
    status_code: int | None = None,
) -> Event:
    """Reactor POST to Stargate team_dispatch failed for a owed hop."""
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "hop_seq": hop_seq,
        "hop_reason": hop_reason,
        "error": error,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    return Event(
        signal="frontier.sdk.conductor.hop.admit_failed",
        payload=payload,
        scope="node",
    )


@event_factory
def FrontierSdkConductorHopParked(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    reason: str,
) -> Event:
    """Hop budget exhausted; mission parked on worker thread."""
    return Event(
        signal="frontier.sdk.conductor.hop.parked",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "hop_seq": hop_seq,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def FrontierSdkConductorHopSkipped(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    gate: str,
) -> Event:
    """Reactor exited without POST — observability for silent skip paths."""
    return Event(
        signal="frontier.sdk.conductor.hop.skipped",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "hop_seq": hop_seq,
            "gate": gate,
        },
        scope="node",
    )


@event_factory
def FrontierSdkConductorHopParkHarvest(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    summoning_thread_id: str,
    hop_seq: int,
) -> Event:
    """Terminal park-harvest classification — harvest still owed after exit-persist."""
    return Event(
        signal="frontier.sdk.conductor.hop.park_harvest",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "summoning_thread_id": summoning_thread_id,
            "hop_seq": hop_seq,
        },
        scope="node",
    )


@event_factory
def FrontierSdkConductorHopWatchdogFired(  # noqa: N802
    last_dispatch_id: str,
    thread_id: str,
    hop_seq: int,
) -> Event:
    """GIW sweep fired a owed successor the reactor did not admit."""
    return Event(
        signal="frontier.sdk.conductor.hop.watchdog_fired",
        payload={
            "last_dispatch_id": last_dispatch_id,
            "thread_id": thread_id,
            "hop_seq": hop_seq,
        },
        scope="node",
    )


def emit_frontier_sdk_conductor_hop_declared(
    *,
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    hop_reason: str,
    row_closed: str | None = None,
) -> None:
    """Publish ROW_HOP closeout observation."""
    if hop_reason not in _HOP_REASONS:
        logger.warning(
            "conductor hop declared with unknown hop_reason=%s dispatch_id=%s",
            hop_reason,
            dispatch_id,
        )
    emit_frontier_event(
        FrontierSdkConductorHopDeclared(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason=hop_reason,
            row_closed=row_closed,
        )
    )


def emit_frontier_sdk_conductor_hop_admitted(
    *,
    predecessor_dispatch_id: str,
    successor_dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    hop_reason: str,
) -> None:
    """Publish successful hop successor admit."""
    emit_frontier_event(
        FrontierSdkConductorHopAdmitted(
            predecessor_dispatch_id=predecessor_dispatch_id,
            successor_dispatch_id=successor_dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason=hop_reason,
        )
    )


def emit_frontier_sdk_conductor_hop_admit_failed(
    *,
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    hop_reason: str,
    error: str,
    status_code: int | None = None,
) -> None:
    """Publish hop reactor admit failure."""
    emit_frontier_event(
        FrontierSdkConductorHopAdmitFailed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            hop_reason=hop_reason,
            error=error,
            status_code=status_code,
        )
    )


def emit_frontier_sdk_conductor_hop_parked(
    *,
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    reason: str,
) -> None:
    """Publish hop budget exhaustion park."""
    emit_frontier_event(
        FrontierSdkConductorHopParked(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            reason=reason,
        )
    )


def emit_frontier_sdk_conductor_hop_skipped(
    *,
    dispatch_id: str,
    thread_id: str,
    hop_seq: int,
    gate: str,
) -> None:
    """Publish hop reactor skip (gate names silent return path)."""
    emit_frontier_event(
        FrontierSdkConductorHopSkipped(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
            gate=gate,
        )
    )


def emit_frontier_sdk_conductor_hop_park_harvest(
    *,
    dispatch_id: str,
    thread_id: str,
    summoning_thread_id: str,
    hop_seq: int,
) -> None:
    """Publish park-harvest classification at terminal (not reply arrival)."""
    emit_frontier_event(
        FrontierSdkConductorHopParkHarvest(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            summoning_thread_id=summoning_thread_id,
            hop_seq=hop_seq,
        )
    )


def emit_frontier_sdk_conductor_hop_watchdog_fired(
    *,
    last_dispatch_id: str,
    thread_id: str,
    hop_seq: int,
) -> None:
    """Publish watchdog-fired hop successor."""
    emit_frontier_event(
        FrontierSdkConductorHopWatchdogFired(
            last_dispatch_id=last_dispatch_id,
            thread_id=thread_id,
            hop_seq=hop_seq,
        )
    )
