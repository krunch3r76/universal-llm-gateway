"""Event contracts for the Auto second-read reflex and premium model binds.

Auto submits nested dispatches straight to the worker, bypassing the Stargate
admit path where ``sdk_cost_risk`` is raised. Every premium bind on this lane is
therefore invisible to the usual cost guard unless it is announced here, so these
signals are what makes reflex spend auditable in dispatch-economics rollups.
"""

from __future__ import annotations

from cursor_capabilities import canonical_cursor_bare_id
from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

# Mirrors the cost-risk roster Stargate's align_cursor_knobs warns on, so the two
# guards cannot drift into disagreeing about what counts as expensive.
_PREMIUM_BARE_MODELS = frozenset(
    {"claude-opus-5", "claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5"}
)


def is_premium_bind(model_id: str) -> bool:
    """True when *model_id* is one of the cost-risk reasoners Stargate warns on."""
    try:
        return canonical_cursor_bare_id(model_id) in _PREMIUM_BARE_MODELS
    except ValueError:
        return False


@event_factory
def FrontierSdkAutoSecondRead(  # noqa: N802
    thread_id: str,
    executor_dispatch_id: str,
    reflex_dispatch_id: str | None,
    fired: bool,
    reason: str,
    model: str | None,
    contract: str,
    outcome: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.second_read",
        payload={
            "thread_id": thread_id,
            "executor_dispatch_id": executor_dispatch_id,
            "reflex_dispatch_id": reflex_dispatch_id,
            "fired": fired,
            "reason": reason,
            "model": model,
            "contract": contract,
            "outcome": outcome,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoPremiumBind(  # noqa: N802
    thread_id: str,
    dispatch_id: str,
    model: str,
    handoff_contract: str,
    lane: str,
    knobs: str | None,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.premium_bind",
        payload={
            "thread_id": thread_id,
            "dispatch_id": dispatch_id,
            "model": model,
            "handoff_contract": handoff_contract,
            "lane": lane,
            "knobs": knobs,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoMechanicalExecutorRedirected(  # noqa: N802
    thread_id: str,
    requested_model: str,
    executor_model: str,
    contract: str,
    handoff_contract: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.mechanical_executor_redirected",
        payload={
            "thread_id": thread_id,
            "requested_model": requested_model,
            "executor_model": executor_model,
            "contract": contract,
            "handoff_contract": handoff_contract,
        },
        scope="node",
    )


def emit_second_read(
    *,
    thread_id: str,
    executor_dispatch_id: str,
    reflex_dispatch_id: str | None,
    fired: bool,
    reason: str,
    model: str | None,
    contract: str,
    outcome: str | None = None,
) -> None:
    """Announce a reflex decision. Observability never breaks a closeout relay."""
    try:
        emit_frontier_event(
            FrontierSdkAutoSecondRead(
                thread_id=thread_id,
                executor_dispatch_id=executor_dispatch_id,
                reflex_dispatch_id=reflex_dispatch_id,
                fired=fired,
                reason=reason,
                model=model,
                contract=contract,
                outcome=outcome,
            )
        )
    except Exception as exc:  # noqa: BLE001 — observation must not raise into relay
        logger.warning("cursor-auto second_read event emit failed: %s", exc)


def emit_premium_bind(
    *,
    thread_id: str,
    dispatch_id: str,
    model: str,
    handoff_contract: str,
    lane: str,
    knobs: str | None = None,
) -> None:
    """Announce a premium bind on the Auto lane (Stargate cost guard is bypassed)."""
    try:
        emit_frontier_event(
            FrontierSdkAutoPremiumBind(
                thread_id=thread_id,
                dispatch_id=dispatch_id,
                model=model,
                handoff_contract=handoff_contract,
                lane=lane,
                knobs=knobs,
            )
        )
    except Exception as exc:  # noqa: BLE001 — observation must not raise into relay
        logger.warning("cursor-auto premium_bind event emit failed: %s", exc)


def emit_mechanical_executor_redirected(
    *,
    thread_id: str,
    requested_model: str,
    executor_model: str,
    contract: str,
    handoff_contract: str,
) -> None:
    """Announce that mechanical work was moved off a reasoning model.

    The redirect is silent on the wire otherwise — the orchestrator asked for one
    executor and a different one ran. Announcing it keeps the substitution
    auditable and makes mis-routing measurable rather than merely prevented.
    """
    try:
        emit_frontier_event(
            FrontierSdkAutoMechanicalExecutorRedirected(
                thread_id=thread_id,
                requested_model=requested_model,
                executor_model=executor_model,
                contract=contract,
                handoff_contract=handoff_contract,
            )
        )
    except Exception as exc:  # noqa: BLE001 — observation must not raise into relay
        logger.warning(
            "cursor-auto mechanical_executor_redirected event emit failed: %s", exc
        )


def maybe_emit_premium_bind(
    *,
    thread_id: str,
    dispatch_id: str,
    model: str,
    handoff_contract: str,
    lane: str,
    knobs: dict[str, str] | None = None,
) -> None:
    """Announce *model* only when it is premium; a no-op otherwise."""
    if not is_premium_bind(model):
        return
    emit_premium_bind(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        model=model,
        handoff_contract=handoff_contract,
        lane=lane,
        knobs=",".join(f"{k}={v}" for k, v in sorted((knobs or {}).items())) or None,
    )


__all__ = [
    "FrontierSdkAutoMechanicalExecutorRedirected",
    "FrontierSdkAutoPremiumBind",
    "FrontierSdkAutoSecondRead",
    "emit_mechanical_executor_redirected",
    "emit_premium_bind",
    "emit_second_read",
    "is_premium_bind",
    "maybe_emit_premium_bind",
]
