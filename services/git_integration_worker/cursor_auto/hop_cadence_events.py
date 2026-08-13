"""Observation signals for hop-cadence stall-revoke, succession confirm, and refuse lifecycle accounting."""

from __future__ import annotations

from claude_bundles.hop_cadence_lease_events import (
    emit_fence_started as emit_fence_started,
)
from claude_bundles.hop_cadence_lease_events import (
    emit_identity_bound as emit_identity_bound,
)
from claude_bundles.hop_cadence_lease_events import (
    emit_lease_lost as emit_lease_lost,
)
from claude_bundles.hop_cadence_lease_events import (
    emit_lease_reclaimed as emit_lease_reclaimed,
)
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


@event_factory
def GiwCursorAutoHopCadenceReleaseDeferred(  # noqa: N802
    execution_id: str,
    reason: str,
    idle_streak: int,
    thread_id: str = "",
) -> Event:
    """Succession release refused this tick; reason is the running-split or idle-streak gate."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_release_deferred",
        payload={
            "thread_id": thread_id,
            "execution_id": execution_id,
            "reason": reason,
            "idle_streak": idle_streak,
        },
        scope="node",
        role="observation",
    )


def emit_release_deferred(
    *,
    execution_id: str,
    reason: str,
    idle_streak: int,
    thread_id: str = "",
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_release_deferred`` when abort is refused."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceReleaseDeferred(
            execution_id=execution_id,
            reason=reason,
            idle_streak=idle_streak,
            thread_id=thread_id,
        )
    )
    logger.info(
        "hop_cadence release_deferred exec=%s reason=%s idle_streak=%s",
        execution_id,
        reason,
        idle_streak,
    )


@event_factory
def GiwCursorAutoHopCadenceBindingIndeterminate(  # noqa: N802
    thread_id: str,
    reason: str,
) -> Event:
    """Predecessor binding could not be resolved; not a first-seat claim."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_binding_indeterminate",
        payload={"thread_id": thread_id, "reason": reason},
        scope="node",
        role="observation",
    )


@event_factory
def GiwCursorAutoHopCadenceSeatRebound(  # noqa: N802
    thread_id: str,
    prior_registration_id: str,
    new_registration_id: str,
    superseded_execution_id: str,
) -> Event:
    """Seat holder on a lane rebound to the successor registration."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_seat_rebound",
        payload={
            "thread_id": thread_id,
            "prior_registration_id": prior_registration_id,
            "new_registration_id": new_registration_id,
            "superseded_execution_id": superseded_execution_id,
        },
        scope="node",
        role="observation",
    )


def emit_binding_indeterminate(*, thread_id: str, reason: str) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_binding_indeterminate``."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceBindingIndeterminate(thread_id=thread_id, reason=reason)
    )
    logger.warning(
        "hop_cadence binding_indeterminate thread=%s reason=%s",
        thread_id,
        reason,
    )


def emit_seat_rebound(
    *,
    thread_id: str,
    prior_registration_id: str,
    new_registration_id: str,
    superseded_execution_id: str,
) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_seat_rebound`` after holder advances."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceSeatRebound(
            thread_id=thread_id,
            prior_registration_id=prior_registration_id,
            new_registration_id=new_registration_id,
            superseded_execution_id=superseded_execution_id,
        )
    )
    logger.info(
        "hop_cadence seat_rebound thread=%s prior=%s new=%s superseded_exec=%s",
        thread_id,
        prior_registration_id,
        new_registration_id,
        superseded_execution_id,
    )
    prior = (prior_registration_id or "").strip()
    if prior and not prior.startswith("__none:"):
        try:
            from claude_bundles.cse_wake_retain import (
                discharge_superseded_seat_obligations,
            )

            discharge_superseded_seat_obligations(
                prior,
                successor_registration_id=new_registration_id,
            )
        except Exception as exc:  # noqa: BLE001 — rebind must not crash cadence
            logger.warning(
                "hop_cadence seat_rebound obligation discharge failed prior=%s: %s",
                prior,
                exc,
            )


@event_factory
def GiwCursorAutoHopCadenceOverlap(  # noqa: N802
    lane: str,
    execution_ids: list[str],
) -> Event:
    """≥2 operator-purpose streams on one recorded lane (census OVERLAP)."""
    return Event(
        signal="giw.cursor_auto.hop_cadence_overlap",
        payload={"lane": lane, "execution_ids": execution_ids},
        scope="node",
        role="observation",
    )


def emit_overlap(*, lane: str, execution_ids: list[str]) -> None:
    """Emit ``giw.cursor_auto.hop_cadence_overlap`` for a census OVERLAP finding."""
    emit_frontier_event(
        GiwCursorAutoHopCadenceOverlap(lane=lane, execution_ids=execution_ids)
    )
    logger.warning(
        "hop_cadence overlap lane=%s execution_ids=%s",
        lane,
        execution_ids,
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
    "emit_binding_indeterminate",
    "emit_cadence_refuse",
    "emit_fence_started",
    "emit_identity_bound",
    "emit_lease_lost",
    "emit_lease_reclaimed",
    "emit_overlap",
    "emit_registration_advanced",
    "emit_release_deferred",
    "emit_revoke_breaker",
    "emit_seat_rebound",
    "emit_succession_confirmed",
    "emit_succession_revoked",
]
