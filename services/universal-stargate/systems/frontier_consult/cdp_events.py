"""CDP generate substrate lifecycle events covering admit, submit, proof,
stall, horizon retain, and on-behalf delivery.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

logger = get_logger(__name__)

# (signal, reason) pairs already reported; keeps a dead publisher to one line.
_SWALLOWED_SEEN: set[tuple[str, str]] = set()
# Once-per-execution_id: reconcile ticks every 20s on retained-past-horizon legs.
_HORIZON_UNVERIFIABLE_EMITTED: set[str] = set()


@event_factory
def CdpGenerateAdmitted(  # noqa: N802
    request_id: str,
    execution_id: str,
    model: str,
    thread_id: str,
) -> Event:
    """CDP generate admitted; worker task spawned."""
    return Event(
        signal="cdp.generate.admitted",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "model": model,
            "thread_id": thread_id,
        },
        scope="node",
    )


@event_factory
def CdpGenerateSubmitted(  # noqa: N802
    request_id: str,
    execution_id: str,
    satellite_execution_id: str,
    model: str,
) -> Event:
    """Satellite project-ask accepted the submit."""
    return Event(
        signal="cdp.generate.submitted",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
            "model": model,
        },
        scope="node",
    )


@event_factory
def CdpGenerateProof(  # noqa: N802
    request_id: str,
    execution_id: str,
    satellite_execution_id: str | None,
    archive_uri: str | None = None,
    content_proof_uri: str | None = None,
) -> Event:
    """Harvest proof present (archive or content_proof)."""
    return Event(
        signal="cdp.generate.proof",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
            "archive_uri": archive_uri,
            "content_proof_uri": content_proof_uri,
        },
        scope="node",
    )


@event_factory
def CdpGenerateStalled(  # noqa: N802
    request_id: str,
    execution_id: str,
    satellite_execution_id: str | None,
    stall_stage: str | None,
    error: str | None = None,
    progress_trace: dict[str, Any] | None = None,
    archive_uri: str | None = None,
    deliverable_present: bool = False,
    since_last_progress_s: float | None = None,
) -> Event:
    """CDP generate terminated without proof (stall or satellite failure).

    ``progress_trace`` carries the poll-loop fingerprint history on aborts we
    raise ourselves (``wall_clock_exceeded``, ``no_progress``); without it
    those stages cannot distinguish a long task from a dead session.

    ``wall_clock_exceeded`` means no observed fingerprint progress for
    ``max_wall_s`` seconds (not cumulative job elapsed time).

    ``since_last_progress_s`` is the poller-local idle span at abort
    (``clock() - last_progress_at``). ``archive_uri`` / ``deliverable_present``
    distinguish residual stalls where a deliverable URI was recovered but proof
    fields were insufficient.
    """
    return Event(
        signal="cdp.generate.stalled",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
            "stall_stage": stall_stage,
            "error": error,
            "progress_trace": progress_trace,
            "archive_uri": archive_uri,
            "deliverable_present": deliverable_present,
            "since_last_progress_s": since_last_progress_s,
        },
        scope="node",
    )


@event_factory
def CdpGenerateHorizonUnverifiable(  # noqa: N802
    request_id: str,
    execution_id: str,
    satellite_execution_id: str | None,
    thread_id: str,
    stall_stage: str,
    error: str | None = None,
) -> Event:
    """Non-terminal horizon retain: attach unverifiable, not generate death.

    Distinct from ``cdp.generate.stalled`` so hop cadence, the inflight ledger
    terminal set, and dispatch-monitor crit do not treat retain as FAILED.
    ``stall_stage`` is the sizing filter (``horizon_unverifiable_retained`` vs
    ``horizon_seated_authorship``). First successful publish per
    ``execution_id`` per process; a swallowed attempt does not consume the
    slot.
    """
    return Event(
        signal="cdp.generate.horizon.unverifiable",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
            "thread_id": thread_id,
            "stall_stage": stall_stage,
            "error": error,
        },
        scope="node",
    )


@event_factory
def CdpGenerateReconciled(  # noqa: N802
    request_id: str,
    execution_id: str,
    satellite_execution_id: str | None,
    via: str = "reconcile",
) -> Event:
    """Non-terminal observation when reconcile finalizes a zombie leg."""
    return Event(
        signal="cdp.generate.reconciled",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
            "via": via,
        },
        scope="node",
    )


@event_factory
def CdpGenerateDeliveryFailed(  # noqa: N802
    request_id: str,
    execution_id: str,
    thread_id: str,
    stall_stage: str | None = None,
) -> Event:
    """On-behalf bus delivery failed after harvest or failure body."""
    return Event(
        signal="cdp.generate.delivery_failed",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "thread_id": thread_id,
            "stall_stage": stall_stage,
        },
        scope="node",
    )


def publish_cdp_event(event: Event) -> bool:
    """Best-effort publish via Stargate proxy event bus.

    Returns True when ``publish_from_sync`` ran. False when the proxy has no
    bus or publish raised. Horizon sizing retries on False; other callers
    ignore the return (observability must not fail the lane).
    """
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is None:
            _warn_swallowed(event.signal, "proxy has no event_bus")
            return False
        event_bus.publish_from_sync(event)
        return True
    except Exception as exc:  # noqa: BLE001 — observability must not fail the lane
        _warn_swallowed(event.signal, f"{type(exc).__name__}: {exc}")
        return False


def publish_cdp_kwargs(factory: Any, **kwargs: Any) -> bool:
    """Build + publish a CDP event factory (swallow publish errors).

    Returns False only on a failed delivery (bus missing, publish raised, or
    factory raised). None-returning test stubs count as delivered.
    """
    try:
        delivered = publish_cdp_event(factory(**kwargs))
        if delivered is False:
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        _warn_swallowed(
            getattr(factory, "__name__", "cdp.generate.?"),
            f"{type(exc).__name__}: {exc}",
        )
        return False


def publish_horizon_unverifiable_once(
    *,
    request_id: str,
    execution_id: str,
    satellite_execution_id: str | None,
    thread_id: str,
    stall_stage: str,
    error: str | None,
) -> bool:
    """Publish first successful horizon-unverifiable event for ``execution_id``.

    Returns True when this call delivered the event. A swallowed publish does
    not consume the slot — the next 20s reconcile tick retries. After the
    first successful publish, later ticks are silent (unique-leg arrival,
    not dwell). Lost if we marked-before-publish: the outage burst itself.
    """
    if execution_id in _HORIZON_UNVERIFIABLE_EMITTED:
        return False
    delivered = publish_cdp_kwargs(
        CdpGenerateHorizonUnverifiable,
        request_id=request_id,
        execution_id=execution_id,
        satellite_execution_id=satellite_execution_id,
        thread_id=thread_id,
        stall_stage=stall_stage,
        error=error,
    )
    if delivered is False:
        return False
    _HORIZON_UNVERIFIABLE_EMITTED.add(execution_id)
    return True


def reset_horizon_unverifiable_emits_for_tests() -> None:
    """Clear the once-per-execution_id emit set so reconcile tests start blank."""
    _HORIZON_UNVERIFIABLE_EMITTED.clear()


def _warn_swallowed(signal: str, reason: str) -> None:
    """Log a dropped CDP event once per (signal, reason) for this process.

    A silent swallow here is what made the 2026-07-30/31 emission blackout
    unattributable: 67 legs produced no ``cdp.generate.*`` event and no trace
    of why. Rate-limited to one line per distinct cause so a persistently dead
    publisher stays legible instead of flooding.
    """
    key = (signal, reason)
    if key in _SWALLOWED_SEEN:
        return
    _SWALLOWED_SEEN.add(key)
    logger.warning(
        "cdp event dropped (first occurrence this process): signal=%s reason=%s",
        signal,
        reason,
    )
