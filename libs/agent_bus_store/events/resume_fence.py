"""Event factories for resume fence authority transitions."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusResumeFenceArmed(  # noqa: N802
    fence_id: str,
    root_thread: str,
    transcript_id: str | None,
    source: str,
) -> Event:
    """Signal: mcp.agentbus.resume.fence.armed"""
    return Event(
        signal="mcp.agentbus.resume.fence.armed",
        payload={
            "fence_id": fence_id,
            "root_thread": root_thread,
            "transcript_id": transcript_id,
            "source": source,
        },
        role="coordination",
    )


@event_factory
def AgentBusResumeFencePoured(  # noqa: N802
    fence_id: str,
    root_thread: str,
    bundle_bytes: int,
    readable_counts: dict[str, Any],
    seal_status: str,
    mission_bytes: int = 0,
    card_inlined: bool = True,
    bundle_version: str = "resume-bundle-v1",
) -> Event:
    """Signal: mcp.agentbus.resume.fence.poured"""
    return Event(
        signal="mcp.agentbus.resume.fence.poured",
        payload={
            "fence_id": fence_id,
            "root_thread": root_thread,
            "bundle_bytes": bundle_bytes,
            "readable_counts": readable_counts,
            "seal_status": seal_status,
            "mission_bytes": mission_bytes,
            "card_inlined": card_inlined,
            "bundle_version": bundle_version,
        },
        role="coordination",
    )


@event_factory
def AgentBusResumeFenceDenied(  # noqa: N802
    fence_id: str,
    surface: str,
    tool: str,
    op: str,
    target: str,
    reason: str,
) -> Event:
    """Signal: mcp.agentbus.resume.fence.denied"""
    return Event(
        signal="mcp.agentbus.resume.fence.denied",
        payload={
            "fence_id": fence_id,
            "surface": surface,
            "tool": tool,
            "op": op,
            "target": target,
            "reason": reason,
        },
        role="coordination",
    )


@event_factory
def AgentBusResumeFenceCitationRefused(  # noqa: N802
    fence_id: str,
    foreign: list[str],
    turn_subject: str,
) -> Event:
    """Signal: mcp.agentbus.resume.fence.citation_refused"""
    return Event(
        signal="mcp.agentbus.resume.fence.citation_refused",
        payload={
            "fence_id": fence_id,
            "foreign": foreign,
            "turn_subject": turn_subject,
        },
        role="coordination",
    )


@event_factory
def AgentBusResumeFenceReleased(  # noqa: N802
    fence_id: str,
    release_turn: int,
) -> Event:
    """Signal: mcp.agentbus.resume.fence.released"""
    return Event(
        signal="mcp.agentbus.resume.fence.released",
        payload={
            "fence_id": fence_id,
            "release_turn": release_turn,
        },
        role="coordination",
    )


@event_factory
def AgentBusResumeFenceExpired(  # noqa: N802
    fence_id: str,
    idle_seconds: int,
) -> Event:
    """Signal: mcp.agentbus.resume.fence.expired"""
    return Event(
        signal="mcp.agentbus.resume.fence.expired",
        payload={
            "fence_id": fence_id,
            "idle_seconds": idle_seconds,
        },
        role="coordination",
    )


def emit_resume_fence_armed(
    *,
    fence_id: str,
    root_thread: str,
    transcript_id: str | None,
    source: str,
) -> None:
    event = AgentBusResumeFenceArmed(
        fence_id=fence_id,
        root_thread=root_thread,
        transcript_id=transcript_id,
        source=source,
    )
    _publish(event.signal, event.payload, role=event.role)


def emit_resume_fence_poured(
    *,
    fence_id: str,
    root_thread: str,
    bundle_bytes: int,
    readable_counts: dict[str, Any],
    seal_status: str,
    mission_bytes: int = 0,
    card_inlined: bool = True,
    bundle_version: str = "resume-bundle-v1",
) -> None:
    event = AgentBusResumeFencePoured(
        fence_id=fence_id,
        root_thread=root_thread,
        bundle_bytes=bundle_bytes,
        readable_counts=readable_counts,
        seal_status=seal_status,
        mission_bytes=mission_bytes,
        card_inlined=card_inlined,
        bundle_version=bundle_version,
    )
    _publish(event.signal, event.payload, role=event.role)


def emit_resume_fence_denied(
    *,
    fence_id: str,
    surface: str,
    tool: str,
    op: str,
    target: str,
    reason: str,
) -> None:
    event = AgentBusResumeFenceDenied(
        fence_id=fence_id,
        surface=surface,
        tool=tool,
        op=op,
        target=target,
        reason=reason,
    )
    _publish(event.signal, event.payload, role=event.role)


def emit_resume_fence_citation_refused(
    *,
    fence_id: str,
    foreign: list[str],
    turn_subject: str,
) -> None:
    event = AgentBusResumeFenceCitationRefused(
        fence_id=fence_id,
        foreign=foreign,
        turn_subject=turn_subject,
    )
    _publish(event.signal, event.payload, role=event.role)


def emit_resume_fence_released(*, fence_id: str, release_turn: int) -> None:
    event = AgentBusResumeFenceReleased(
        fence_id=fence_id,
        release_turn=release_turn,
    )
    _publish(event.signal, event.payload, role=event.role)


def emit_resume_fence_expired(*, fence_id: str, idle_seconds: int) -> None:
    event = AgentBusResumeFenceExpired(
        fence_id=fence_id,
        idle_seconds=idle_seconds,
    )
    _publish(event.signal, event.payload, role=event.role)
