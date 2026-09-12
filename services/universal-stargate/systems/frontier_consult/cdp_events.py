"""CDP generate substrate lifecycle events covering admit, submit, proof,
stall, horizon retain, and on-behalf delivery.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from . import cdp_event_publish

# Test fixtures clear swallow dedupe via this alias (lives in cdp_event_publish).
_SWALLOWED_SEEN = cdp_event_publish._SWALLOWED_SEEN


def publish_cdp_event(event: Event) -> bool:
    """Delegate to ``cdp_event_publish`` (patchable via this module in tests)."""
    return cdp_event_publish.publish_cdp_event(event)


def publish_cdp_kwargs(factory: Any, **kwargs: Any) -> bool:
    """Delegate to ``cdp_event_publish`` (patchable via this module in tests)."""
    return cdp_event_publish.publish_cdp_kwargs(factory, **kwargs)

__all__ = [
    "CdpGenerateAdmitted",
    "CdpGenerateSubmitted",
    "CdpGenerateSeated",
    "CdpGenerateProof",
    "CdpGenerateStalled",
    "CdpGenerateHorizonUnverifiable",
    "CdpGenerateReconciled",
    "CdpGenerateDeliveryFailed",
    "publish_cdp_event",
    "publish_cdp_kwargs",
    "publish_horizon_unverifiable_once",
    "reset_horizon_unverifiable_emits_for_tests",
]
# Once-per-execution_id: reconcile ticks every 20s on retained-past-horizon legs.
_HORIZON_UNVERIFIABLE_EMITTED: set[str] = set()


@event_factory
def CdpGenerateAdmitted(  # noqa: N802
    request_id: str,
    execution_id: str,
    model: str,
    thread_id: str,
    topic: str | None = None,
) -> Event:
    """CDP generate admitted; worker task spawned."""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "execution_id": execution_id,
        "model": model,
        "thread_id": thread_id,
    }
    if topic:
        payload["topic"] = topic
    return Event(
        signal="cdp.generate.admitted",
        payload=payload,
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
def CdpGenerateSeated(  # noqa: N802
    request_id: str,
    execution_id: str,
    satellite_execution_id: str | None,
    registration_id: str | None,
    chat_url: str,
    seating_ordinal: int,
    observed_at: str,
    terminal: bool = False,
) -> Event:
    """First observation of a CSE chat URL inside generate poll scope.

    Non-terminal mid-flight rung: dispatch-monitor and consult_watch treat
    this as liveness only — not proof, stall, or delivery failure.
    ``seating_ordinal`` is advisory (successor hops on the same execution_id).
    """
    return Event(
        signal="cdp.generate.seated",
        payload={
            "request_id": request_id,
            "execution_id": execution_id,
            "satellite_execution_id": satellite_execution_id,
            "registration_id": registration_id,
            "chat_url": chat_url,
            "seating_ordinal": seating_ordinal,
            "observed_at": observed_at,
            "terminal": terminal,
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
    via: str = "worker",
    attested_by: str | None = None,
    thread_id: str | None = None,
    pointer_turn: int | None = None,
    dispatch_link_terminal: bool | None = None,
    registration_id: str | None = None,
    chat_url: str | None = None,
) -> Event:
    """Harvest proof present (archive or content_proof)."""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "execution_id": execution_id,
        "satellite_execution_id": satellite_execution_id,
        "archive_uri": archive_uri,
        "content_proof_uri": content_proof_uri,
        "via": via,
    }
    if attested_by is not None:
        payload["attested_by"] = attested_by
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if pointer_turn is not None:
        payload["pointer_turn"] = pointer_turn
    if dispatch_link_terminal is not None:
        payload["dispatch_link_terminal"] = dispatch_link_terminal
    if registration_id is not None:
        payload["registration_id"] = registration_id
    if chat_url is not None:
        payload["chat_url"] = chat_url
    return Event(
        signal="cdp.generate.proof",
        payload=payload,
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
    thread_id: str | None = None,
    pointer_turn: int | None = None,
    dispatch_link_terminal: bool | None = None,
    registration_id: str | None = None,
    chat_url: str | None = None,
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
            **{
                k: v
                for k, v in (
                    ("thread_id", thread_id),
                    ("pointer_turn", pointer_turn),
                    ("dispatch_link_terminal", dispatch_link_terminal),
                    ("registration_id", registration_id),
                    ("chat_url", chat_url),
                )
                if v is not None
            },
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
    http_status: int | None = None,
    detail_preview: str | None = None,
    archive_uri: str | None = None,
) -> Event:
    """On-behalf bus delivery failed after harvest or failure body."""
    payload: dict[str, Any] = {
        "request_id": request_id,
        "execution_id": execution_id,
        "thread_id": thread_id,
        "stall_stage": stall_stage,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    if detail_preview is not None:
        payload["detail_preview"] = detail_preview
    if archive_uri is not None:
        payload["archive_uri"] = archive_uri
    return Event(
        signal="cdp.generate.delivery_failed",
        payload=payload,
        scope="node",
    )


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
