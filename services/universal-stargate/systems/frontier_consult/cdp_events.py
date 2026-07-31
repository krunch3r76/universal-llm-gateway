"""CDP substrate lifecycle events (submit / proof / stall / delivery)."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory


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
) -> Event:
    """CDP generate terminated without proof (stall or satellite failure).

    ``progress_trace`` carries the poll-loop fingerprint history on aborts we
    raise ourselves (``wall_clock_exceeded``, ``no_progress``); without it
    those stages cannot distinguish a long task from a dead session.
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


def publish_cdp_event(event: Event) -> None:
    """Best-effort publish via Stargate proxy event bus (no-op if unavailable)."""
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is None:
            return
        event_bus.publish_from_sync(event)
    except Exception:  # noqa: BLE001 — observability must not fail the lane
        return


def publish_cdp_kwargs(factory: Any, **kwargs: Any) -> None:
    """Build + publish a CDP event factory (swallow publish errors)."""
    try:
        publish_cdp_event(factory(**kwargs))
    except Exception:  # noqa: BLE001
        return
