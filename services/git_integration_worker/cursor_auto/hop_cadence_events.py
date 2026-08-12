"""Observation signals for hop-cadence stall-revoke, succession confirm, and refuse lifecycle accounting."""

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
    """Cadence hop breaker tripped (stall revocations or unjoinable hop failures)."""
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


@event_factory
def GiwCursorAutoHopCadenceSuccessionConfirmed(  # noqa: N802
    thread_id: str,
    matched_key: str,
    watch_registration_id: str,
    prior_registration_id: str,
    superseded_execution_id: str,
) -> Event:
    """Live active-work membership first intersects a watch succession claim."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_succession_confirmed",
        payload={
            "thread_id": thread_id,
            "matched_key": matched_key,
            "watch_registration_id": watch_registration_id,
            "prior_registration_id": prior_registration_id,
            "superseded_execution_id": superseded_execution_id,
            "membership_scope": "cdp_ask.active_work",
            "freshness": "snapshot_at_scan",
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceRegistrationAdvanced(  # noqa: N802
    thread_id: str,
    prior_registration_id: str,
    new_registration_id: str,
    superseding_execution_id: str,
    superseded_execution_id: str,
) -> Event:
    """Watch ``registration_id`` advanced to the matched active-work row."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_registration_advanced",
        payload={
            "thread_id": thread_id,
            "prior_registration_id": prior_registration_id,
            "new_registration_id": new_registration_id,
            "superseding_execution_id": superseding_execution_id,
            "superseded_execution_id": superseded_execution_id,
        },
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceRefuse(  # noqa: N802
    thread_id: str,
    reason: str,
    registration_id: str,
    signal: str,
) -> Event:
    """Cadence hop refused at request/fire time while incumbent registration streams."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_refuse",
        payload={
            "thread_id": thread_id,
            "reason": reason,
            "registration_id": registration_id,
            "signal": signal,
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
    """Emit ``giw.cursor_auto.hop_cadence_succession_revoked`` after a joinable stall."""
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
    """Emit ``giw.cursor_auto.hop_cadence_revoke_breaker`` when the breaker trips."""
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


def emit_succession_confirmed(
    *,
    thread_id: str,
    matched_key: str,
    watch_registration_id: str,
    prior_registration_id: str,
    superseded_execution_id: str,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_succession_confirmed`` on first live membership."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceSuccessionConfirmed(
            thread_id=thread_id,
            matched_key=matched_key,
            watch_registration_id=watch_registration_id,
            prior_registration_id=prior_registration_id,
            superseded_execution_id=superseded_execution_id,
        )
    )
    logger.info(
        "hop_cadence succession_confirmed thread=%s matched_key=%s watch_reg=%s "
        "prior_reg=%s superseded_exec=%s",
        thread_id,
        matched_key,
        watch_registration_id,
        prior_registration_id,
        superseded_execution_id,
    )


def emit_registration_advanced(
    *,
    thread_id: str,
    prior_registration_id: str,
    new_registration_id: str,
    superseding_execution_id: str,
    superseded_execution_id: str,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_registration_advanced`` once per id transition."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceRegistrationAdvanced(
            thread_id=thread_id,
            prior_registration_id=prior_registration_id,
            new_registration_id=new_registration_id,
            superseding_execution_id=superseding_execution_id,
            superseded_execution_id=superseded_execution_id,
        )
    )
    logger.info(
        "hop_cadence registration_advanced thread=%s prior=%s new=%s key=%s superseded_exec=%s",
        thread_id,
        prior_registration_id,
        new_registration_id,
        superseding_execution_id,
        superseded_execution_id,
    )


def emit_cadence_refuse(
    *,
    thread_id: str,
    reason: str,
    registration_id: str,
    signal: str,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_refuse`` when cadence refuses a repeat hop."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceRefuse(
            thread_id=thread_id,
            reason=reason,
            registration_id=registration_id,
            signal=signal,
        )
    )
    logger.warning(
        "hop_cadence refuse thread=%s reason=%s registration_id=%s signal=%s",
        thread_id,
        reason,
        registration_id,
        signal,
    )


__all__ = [
    "emit_cadence_refuse",
    "emit_registration_advanced",
    "emit_revoke_breaker",
    "emit_succession_confirmed",
    "emit_succession_revoked",
]
