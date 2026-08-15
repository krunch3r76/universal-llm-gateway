"""Advisory observations for CDP mission lane provenance.

Gate A of the lane-provenance arc is observe-only: a mission whose lane the
caller never named is recorded, not refused. ``default_operator_seat_binding``
silently backfills ``parent_thread`` from the generate ``thread_id``, which
makes a synthesized association indistinguishable from a caller-declared one
downstream. This signal is what tells us whether refusing the synthesis later
would break real callers.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from .cursor_sdk_generate_signals import publish_frontier_event


@event_factory
def FrontierCdpMissionProvenance(  # noqa: N802
    purpose: str,
    dispatch_thread_id: str,
    parent_thread: str,
    mission_kind: str,
    synthesized: bool,
) -> Event:
    """CDP mission bound a lane the caller did not fully declare."""
    return Event(
        signal="frontier.cdp.mission.provenance",
        payload={
            "purpose": purpose,
            "dispatch_thread_id": dispatch_thread_id,
            "parent_thread": parent_thread,
            "mission_kind": mission_kind,
            "synthesized": synthesized,
        },
        scope="node",
    )


def observe_mission_binding(
    *,
    purpose: str | None,
    dispatch_thread_id: str | None,
    parent_thread: str | None,
    mission_kind: str | None,
    synthesized: bool,
) -> None:
    """Record a mission lane that was synthesized or left unparented.

    A caller-declared ``parent_thread`` is the healthy path and stays silent, so
    the signal's volume is the size of the future enforcement blast radius.
    """
    if purpose not in {"mission", "operator-proxy"}:
        return
    if not synthesized and parent_thread:
        return
    publish_frontier_event(
        FrontierCdpMissionProvenance(
            purpose=purpose,
            dispatch_thread_id=dispatch_thread_id or "",
            parent_thread=parent_thread or "",
            mission_kind=mission_kind or "",
            synthesized=synthesized,
        )
    )
