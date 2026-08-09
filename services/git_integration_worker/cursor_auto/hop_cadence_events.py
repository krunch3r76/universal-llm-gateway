"""Observation signals for hop-cadence stall-revoke accounting (arc 6928 Route A)."""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def GiwCursorAutoHopCadenceSuccessionRevoked(  # noqa: N802
    thread_id: str,
    execution_id: str,
    stall_stage: str | None,
    revocation_count: int,
) -> Event:
    """Succession claim revoked after joinable ``cdp.generate.stalled``."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_succession_revoked",
        payload={
            "thread_id": thread_id,
            "execution_id": execution_id,
            "stall_stage": stall_stage,
            "revocation_count": revocation_count,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceRevokeBreaker(  # noqa: N802
    thread_id: str,
    revocation_count: int,
    breaker_n: int,
) -> Event:
    """Cadence hop breaker tripped after repeated stall revocations."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_revoke_breaker",
        payload={
            "thread_id": thread_id,
            "revocation_count": revocation_count,
            "breaker_n": breaker_n,
        },
        scope="node",
        role="observation",
    )


def emit_succession_revoked(
    *,
    thread_id: str,
    execution_id: str,
    stall_stage: str | None,
    revocation_count: int,
) -> None:
    emit_frontier_event(
        GiwCursorAutoHopCadenceSuccessionRevoked(
            thread_id=thread_id,
            execution_id=execution_id,
            stall_stage=stall_stage,
            revocation_count=revocation_count,
        )
    )
    logger.info(
        "hop_cadence succession_revoked thread=%s execution_id=%s count=%s",
        thread_id,
        execution_id,
        revocation_count,
    )


def emit_revoke_breaker(
    *,
    thread_id: str,
    revocation_count: int,
    breaker_n: int,
) -> None:
    emit_frontier_event(
        GiwCursorAutoHopCadenceRevokeBreaker(
            thread_id=thread_id,
            revocation_count=revocation_count,
            breaker_n=breaker_n,
        )
    )
    logger.warning(
        "hop_cadence revoke_breaker thread=%s count=%s breaker_n=%s",
        thread_id,
        revocation_count,
        breaker_n,
    )


__all__ = [
    "emit_revoke_breaker",
    "emit_succession_revoked",
]
