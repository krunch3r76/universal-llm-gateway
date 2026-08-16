"""Advisory events for directive-loop mission negotiation."""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def FrontierSdkAutoNegotiationOpened(  # noqa: N802
    thread_id: str,
    negotiation_id: str,
    revision: int,
    proposal_hash: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.opened",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "revision": revision,
            "proposal_hash": proposal_hash,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoNegotiationCountered(  # noqa: N802
    thread_id: str,
    negotiation_id: str,
    revision: int,
    proposal_hash: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.countered",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "revision": revision,
            "proposal_hash": proposal_hash,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoNegotiationAgreed(  # noqa: N802
    thread_id: str,
    negotiation_id: str,
    revision: int,
    proposal_hash: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.agreed",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "revision": revision,
            "proposal_hash": proposal_hash,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoNegotiationRatified(  # noqa: N802
    thread_id: str,
    negotiation_id: str,
    revision: int,
    proposal_hash: str,
    agreement_ref: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.ratified",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "revision": revision,
            "proposal_hash": proposal_hash,
            "agreement_ref": agreement_ref,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoNegotiationRefused(  # noqa: N802
    thread_id: str,
    negotiation_id: str | None,
    reason: str,
    revision: int | None = None,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.refused",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "reason": reason,
            "revision": revision,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoNegotiationExpired(  # noqa: N802
    thread_id: str,
    negotiation_id: str,
    revision: int,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.expired",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "revision": revision,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoNegotiationRoundLimited(  # noqa: N802
    thread_id: str,
    negotiation_id: str,
    revision: int,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.negotiation.round_limited",
        payload={
            "thread_id": thread_id,
            "negotiation_id": negotiation_id,
            "revision": revision,
        },
        scope="node",
    )


def _emit(event: Event) -> None:
    try:
        emit_frontier_event(event)
    except Exception:  # noqa: BLE001 — observation must not break relay
        logger.exception("mission negotiation event emit failed signal=%s", event.signal)


def emit_negotiation_opened(**kwargs: str | int) -> None:
    """Fail-soft emit for a first-accepted ``proposal`` opening a ledger row."""
    _emit(FrontierSdkAutoNegotiationOpened(**kwargs))  # type: ignore[arg-type]


def emit_negotiation_countered(**kwargs: str | int) -> None:
    """Fail-soft emit for an accepted ``counter`` revision."""
    _emit(FrontierSdkAutoNegotiationCountered(**kwargs))  # type: ignore[arg-type]


def emit_negotiation_agreed(**kwargs: str | int) -> None:
    """Fail-soft emit for an accepted operator ``agree`` awaiting ratification."""
    _emit(FrontierSdkAutoNegotiationAgreed(**kwargs))  # type: ignore[arg-type]


def emit_negotiation_ratified(**kwargs: str | int) -> None:
    """Fail-soft emit for the terminal accepted ``ratify``."""
    _emit(FrontierSdkAutoNegotiationRatified(**kwargs))  # type: ignore[arg-type]


def emit_negotiation_refused(**kwargs: str | int | None) -> None:
    """Fail-soft emit for any malformed, stale, authority, hash, or scope refusal."""
    _emit(FrontierSdkAutoNegotiationRefused(**kwargs))  # type: ignore[arg-type]


def emit_negotiation_expired(**kwargs: str | int) -> None:
    """Fail-soft emit for an idle-timeout transition to ``EXPIRED``."""
    _emit(FrontierSdkAutoNegotiationExpired(**kwargs))  # type: ignore[arg-type]


def emit_negotiation_round_limited(**kwargs: str | int) -> None:
    """Fail-soft emit for a third-counter attempt hitting the round limit."""
    _emit(FrontierSdkAutoNegotiationRoundLimited(**kwargs))  # type: ignore[arg-type]
