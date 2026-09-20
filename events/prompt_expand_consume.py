"""Caller-door prompt-expand consume routing signals."""

from __future__ import annotations

from typing import Any, Literal

from universal_event_bus import Event, event_factory

Door = Literal["stargate", "giw"]


@event_factory
def ExpandConsumeRouted(  # noqa: N802
    *,
    execution_id: str,
    dispatch_id: str,
    door: Door,
    branch: str,
    reason: str | None,
    fire_hint: str | None = None,
    operator_verb: str | None = None,
    attended: bool = False,
    durable_session: bool = False,
    summoning_thread_id: str | None = None,
) -> Event:
    """Emitted when post-expand consume routing selects a delivery branch."""
    return Event(
        signal="expand.consume.routed",
        payload={
            "execution_id": execution_id,
            "dispatch_id": dispatch_id,
            "door": door,
            "branch": branch,
            "reason": reason,
            "fire_hint": fire_hint,
            "operator_verb": operator_verb,
            "attended": attended,
            "durable_session": durable_session,
            "summoning_thread_id": summoning_thread_id,
        },
        scope="node",
    )


def parse_consume_reason_fields(
    reason: str | None,
) -> tuple[str | None, str | None]:
    """Split ``fire_hint:*`` / ``operator_verb:*`` tokens from router reason strings."""
    if not reason:
        return None, None
    if reason.startswith("fire_hint:"):
        return reason.split(":", 1)[1], None
    if reason.startswith("operator_verb:"):
        return None, reason.split(":", 1)[1]
    return None, None


def emit_expand_consume_routed(
    *,
    execution_id: str,
    dispatch_id: str,
    door: Door,
    branch: str,
    reason: str | None,
    fire_hint: str | None = None,
    operator_verb: str | None = None,
    attended: bool = False,
    durable_session: bool = False,
    summoning_thread_id: str | None = None,
) -> None:
    """Publish ``expand.consume.routed`` from Stargate or GIW caller boundaries."""
    parsed_hint, parsed_verb = parse_consume_reason_fields(reason)
    event = ExpandConsumeRouted(
        execution_id=execution_id,
        dispatch_id=dispatch_id,
        door=door,
        branch=branch,
        reason=reason,
        fire_hint=fire_hint if fire_hint is not None else parsed_hint,
        operator_verb=operator_verb if operator_verb is not None else parsed_verb,
        attended=attended,
        durable_session=durable_session,
        summoning_thread_id=summoning_thread_id,
    )
    _publish(event)


def emit_expand_consume_routed_for_handle(
    handle: Any,
    *,
    door: Door,
    attended: bool,
    durable_session: bool,
    summoning_thread_id: str | None = None,
) -> None:
    """Build event fields from a prepared handle carrying consume routing stamps."""
    branch = getattr(handle, "consume_branch", None)
    if not branch:
        return
    activation = getattr(handle, "consume_activation", None) or {}
    summoning = summoning_thread_id
    if not summoning:
        summoning = activation.get("X-ULG-Summoning-Thread") or getattr(
            handle, "parent_dispatch_thread_id", None
        )
    emit_expand_consume_routed(
        execution_id=str(handle.execution_id),
        dispatch_id=str(handle.dispatch_id),
        door=door,
        branch=str(branch),
        reason=getattr(handle, "consume_reason", None),
        attended=attended,
        durable_session=durable_session,
        summoning_thread_id=summoning,
    )


def _publish(event: Event) -> None:
    try:
        from systems.frontier_consult.cursor_sdk_generate_signals import (
            publish_frontier_event,
        )

        publish_frontier_event(event)
        return
    except Exception:
        pass
    try:
        from services.git_integration_worker.cursor_sdk_events import (
            emit_frontier_event,
        )

        emit_frontier_event(event)
    except Exception:
        return
