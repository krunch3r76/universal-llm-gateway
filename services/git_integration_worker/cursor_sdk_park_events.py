"""``sdk.park.*`` observation events for the steer-restart plane.

Kept apart from ``cursor_sdk_events`` (well past its SLOC ceiling) the same way
``cursor_sdk_cancel_events`` is: one single-responsibility vocabulary module
publishing through the shared registered publisher via ``emit_frontier_event``.

Every park-state transition named in the spec (``requested`` → ``parked`` →
``resume_admitted`` | ``expired``, plus ``refused`` / ``sweep`` /
``bridge_abort_escalated`` / ``resume_refused``) has exactly one emitter here,
so a drain that parks rather than kills is fully observable without logs.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event


def _optional(payload: dict[str, Any], **fields: Any) -> dict[str, Any]:
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    return payload


@event_factory
def SdkParkRequested(  # noqa: N802
    dispatch_id: str,
    thread_id: str | None,
    intent_id: str | None,
    actor: str,
    mode: str,
) -> Event:
    """A live dispatch was asked to park; its bridge run is being cancelled."""
    payload = _optional(
        {"dispatch_id": dispatch_id, "actor": actor, "mode": mode},
        thread_id=thread_id,
        intent_id=intent_id,
    )
    return Event(signal="sdk.park.requested", payload=payload, scope="node")


@event_factory
def SdkParkParked(  # noqa: N802
    dispatch_id: str,
    thread_id: str | None,
    intent_id: str | None,
    method: str,
    tool_call_count: int,
    sidecar_uri: str | None,
    sdk_agent_id_present: bool,
) -> Event:
    """The row reached terminal ``cancelled`` with park columns set (resumable)."""
    payload = _optional(
        {
            "dispatch_id": dispatch_id,
            "method": method,
            "tool_call_count": tool_call_count,
            "sdk_agent_id_present": sdk_agent_id_present,
        },
        thread_id=thread_id,
        intent_id=intent_id,
        sidecar_uri=sidecar_uri,
    )
    return Event(signal="sdk.park.parked", payload=payload, scope="node")


@event_factory
def SdkParkRefused(  # noqa: N802
    dispatch_id: str,
    refusal: str,
    intent_id: str | None,
    actor: str,
) -> Event:
    """Park preflight or cancel ladder refused; nothing was mutated."""
    payload = _optional(
        {"dispatch_id": dispatch_id, "refusal": refusal, "actor": actor},
        intent_id=intent_id,
    )
    return Event(signal="sdk.park.refused", payload=payload, scope="node")


@event_factory
def SdkParkSweep(  # noqa: N802
    intent_id: str,
    drain_epoch: int | None,
    requested: list[str],
    refused: list[dict[str, str]],
    already_parked: list[str],
    live_after: int,
) -> Event:
    """One ``park-for-restart`` sweep summary for a restart intent."""
    payload: dict[str, Any] = {
        "intent_id": intent_id,
        "requested": requested,
        "refused": refused,
        "already_parked": already_parked,
        "live_after": live_after,
    }
    if drain_epoch is not None:
        payload["drain_epoch"] = drain_epoch
    return Event(signal="sdk.park.sweep", payload=payload, scope="node")


@event_factory
def SdkParkBridgeAbortEscalated(  # noqa: N802
    dispatch_id: str,
    intent_id: str | None,
) -> Event:
    """A parked dispatch's bridge lingered past the idle budget and was closed."""
    payload = _optional({"dispatch_id": dispatch_id}, intent_id=intent_id)
    return Event(
        signal="sdk.park.bridge_abort_escalated", payload=payload, scope="node"
    )


@event_factory
def SdkParkResumeAdmitted(  # noqa: N802
    parent_dispatch_id: str,
    child_dispatch_id: str,
    thread_id: str,
    intent_id: str | None,
    code_version: str,
    attempt: int,
) -> Event:
    """GIW admitted a ``resume_of`` child for a parked row (lineage continues)."""
    payload = _optional(
        {
            "parent_dispatch_id": parent_dispatch_id,
            "child_dispatch_id": child_dispatch_id,
            "thread_id": thread_id,
            "code_version": code_version,
            "attempt": attempt,
        },
        intent_id=intent_id,
    )
    return Event(signal="sdk.park.resume_admitted", payload=payload, scope="node")


@event_factory
def SdkParkResumeRefused(  # noqa: N802
    parent_dispatch_id: str,
    reason: str,
    attempt: int,
) -> Event:
    """Auto-resume admission was refused; the park row stays open for the next tick."""
    return Event(
        signal="sdk.park.resume_refused",
        payload={
            "parent_dispatch_id": parent_dispatch_id,
            "reason": reason,
            "attempt": attempt,
        },
        scope="node",
    )


@event_factory
def SdkParkExpired(  # noqa: N802
    parent_dispatch_id: str,
    parked_at: str | None,
    ttl_s: int,
) -> Event:
    """Auto-resume TTL elapsed with no child; manual ``resume_of`` still possible."""
    payload = _optional(
        {"parent_dispatch_id": parent_dispatch_id, "ttl_s": ttl_s},
        parked_at=parked_at,
    )
    return Event(signal="sdk.park.expired", payload=payload, scope="node")


def emit_sdk_park_requested(
    *,
    dispatch_id: str,
    thread_id: str | None,
    intent_id: str | None,
    actor: str,
    mode: str = "cancel",
) -> None:
    emit_frontier_event(
        SdkParkRequested(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            intent_id=intent_id,
            actor=actor,
            mode=mode,
        )
    )


def emit_sdk_park_parked(
    *,
    dispatch_id: str,
    thread_id: str | None,
    intent_id: str | None,
    method: str,
    tool_call_count: int,
    sidecar_uri: str | None,
    sdk_agent_id_present: bool,
) -> None:
    emit_frontier_event(
        SdkParkParked(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            intent_id=intent_id,
            method=method,
            tool_call_count=tool_call_count,
            sidecar_uri=sidecar_uri,
            sdk_agent_id_present=sdk_agent_id_present,
        )
    )


def emit_sdk_park_refused(
    *,
    dispatch_id: str,
    refusal: str,
    intent_id: str | None,
    actor: str,
) -> None:
    emit_frontier_event(
        SdkParkRefused(
            dispatch_id=dispatch_id,
            refusal=refusal,
            intent_id=intent_id,
            actor=actor,
        )
    )


def emit_sdk_park_sweep(
    *,
    intent_id: str,
    drain_epoch: int | None,
    requested: list[str],
    refused: list[dict[str, str]],
    already_parked: list[str],
    live_after: int,
) -> None:
    emit_frontier_event(
        SdkParkSweep(
            intent_id=intent_id,
            drain_epoch=drain_epoch,
            requested=requested,
            refused=refused,
            already_parked=already_parked,
            live_after=live_after,
        )
    )


def emit_sdk_park_bridge_abort_escalated(
    *, dispatch_id: str, intent_id: str | None
) -> None:
    emit_frontier_event(
        SdkParkBridgeAbortEscalated(dispatch_id=dispatch_id, intent_id=intent_id)
    )


def emit_sdk_park_resume_admitted(
    *,
    parent_dispatch_id: str,
    child_dispatch_id: str,
    thread_id: str,
    intent_id: str | None,
    code_version: str,
    attempt: int,
) -> None:
    emit_frontier_event(
        SdkParkResumeAdmitted(
            parent_dispatch_id=parent_dispatch_id,
            child_dispatch_id=child_dispatch_id,
            thread_id=thread_id,
            intent_id=intent_id,
            code_version=code_version,
            attempt=attempt,
        )
    )


def emit_sdk_park_resume_refused(
    *, parent_dispatch_id: str, reason: str, attempt: int
) -> None:
    emit_frontier_event(
        SdkParkResumeRefused(
            parent_dispatch_id=parent_dispatch_id, reason=reason, attempt=attempt
        )
    )


def emit_sdk_park_expired(
    *, parent_dispatch_id: str, parked_at: str | None, ttl_s: int
) -> None:
    emit_frontier_event(
        SdkParkExpired(
            parent_dispatch_id=parent_dispatch_id, parked_at=parked_at, ttl_s=ttl_s
        )
    )


__all__ = [
    "SdkParkBridgeAbortEscalated",
    "SdkParkExpired",
    "SdkParkParked",
    "SdkParkRefused",
    "SdkParkRequested",
    "SdkParkResumeAdmitted",
    "SdkParkResumeRefused",
    "SdkParkSweep",
    "emit_sdk_park_bridge_abort_escalated",
    "emit_sdk_park_expired",
    "emit_sdk_park_parked",
    "emit_sdk_park_refused",
    "emit_sdk_park_requested",
    "emit_sdk_park_resume_admitted",
    "emit_sdk_park_resume_refused",
    "emit_sdk_park_sweep",
]
