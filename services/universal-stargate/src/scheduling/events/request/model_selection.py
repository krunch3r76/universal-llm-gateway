"""Model-selection reputation observation event signals.

Health, score, rank, and sticky-switch allow/suppress events used by profile
selection. Imported via the ``request`` package facade."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

MODEL_SELECTION_HEALTH_OBSERVATION = "model.selection.health.observation"
"""
Runtime health observation ingested for task-scoped model reputation.
Payload: {
    "task": str,
    "model_id": str,
    "outcome": str,
    "latency_ms": float,
    "quality_score": Optional[float],
    "tokens_per_second": Optional[float]
}
"""

MODEL_SELECTION_SCORE_UPDATED = "model.selection.score.updated"
"""
Per-candidate reputation score computed during model selection.
Payload: {
    "task": str,
    "model_id": str,
    "final_score": float,
    "components": dict[str, float]
}
"""

MODEL_SELECTION_RANK_COMPUTED = "model.selection.rank.computed"
"""
Final ranked candidate list produced by reputation-enabled selection.
Payload: {
    "task": str,
    "candidates": list[dict[str, object]],
    "selection_path": str
}
"""

MODEL_SELECTION_SWITCH_SUPPRESSED = "model.selection.switch.suppressed"
"""
Sticky anti-thrash suppressed a marginal top-rank switch.
Payload: {
    "task": str,
    "sticky_key": str,
    "current_model_id": str,
    "contender_model_id": str,
    "delta": float,
    "reason": str
}
"""

MODEL_SELECTION_SWITCH_ALLOWED = "model.selection.switch.allowed"
"""
Anti-thrash evaluated a switch and allowed it (delta >= min_switch_delta).
Payload: {
    "task": str,
    "sticky_key": str,
    "previous_model_id": str,
    "new_model_id": str,
    "delta": float
}
"""


@event_factory
def ModelSelectionHealthObservation(
    *,
    task: str,
    model_id: str,
    outcome: str,
    latency_ms: float,
    quality_score: float | None = None,
    tokens_per_second: float | None = None,
) -> Event:
    """Create MODEL_SELECTION_HEALTH_OBSERVATION event."""
    payload: dict[str, object] = {
        "task": task,
        "model_id": model_id,
        "outcome": outcome,
        "latency_ms": latency_ms,
    }
    if quality_score is not None:
        payload["quality_score"] = quality_score
    if tokens_per_second is not None:
        payload["tokens_per_second"] = tokens_per_second
    return Event(signal=MODEL_SELECTION_HEALTH_OBSERVATION, payload=payload)


@event_factory
def ModelSelectionScoreUpdated(
    *,
    task: str,
    model_id: str,
    final_score: float,
    components: dict[str, float],
) -> Event:
    """Create MODEL_SELECTION_SCORE_UPDATED event."""
    return Event(
        signal=MODEL_SELECTION_SCORE_UPDATED,
        payload={
            "task": task,
            "model_id": model_id,
            "final_score": final_score,
            "components": components,
        },
    )


@event_factory
def ModelSelectionRankComputed(
    *,
    task: str,
    candidates: list[dict[str, object]],
    selection_path: str,
) -> Event:
    """Create MODEL_SELECTION_RANK_COMPUTED event."""
    return Event(
        signal=MODEL_SELECTION_RANK_COMPUTED,
        payload={
            "task": task,
            "candidates": candidates,
            "selection_path": selection_path,
        },
    )


@event_factory
def ModelSelectionSwitchSuppressed(
    *,
    task: str,
    sticky_key: str,
    current_model_id: str,
    contender_model_id: str,
    delta: float,
    reason: str,
) -> Event:
    """Create MODEL_SELECTION_SWITCH_SUPPRESSED event."""
    return Event(
        signal=MODEL_SELECTION_SWITCH_SUPPRESSED,
        payload={
            "task": task,
            "sticky_key": sticky_key,
            "current_model_id": current_model_id,
            "contender_model_id": contender_model_id,
            "delta": delta,
            "reason": reason,
        },
    )


@event_factory
def ModelSelectionSwitchAllowed(
    *,
    task: str,
    sticky_key: str,
    previous_model_id: str,
    new_model_id: str,
    delta: float,
) -> Event:
    """Create MODEL_SELECTION_SWITCH_ALLOWED event."""
    return Event(
        signal=MODEL_SELECTION_SWITCH_ALLOWED,
        payload={
            "task": task,
            "sticky_key": sticky_key,
            "previous_model_id": previous_model_id,
            "new_model_id": new_model_id,
            "delta": delta,
        },
    )
