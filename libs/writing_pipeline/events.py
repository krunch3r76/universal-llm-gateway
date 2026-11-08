"""Domain events for writer-specialist semantic_judge and persona author."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def WritingJudgeCompleted(*, invocation: str, model_id: str) -> Event:  # noqa: N802
    return Event(
        signal="writing.judge.completed",
        payload={"invocation": invocation, "model_id": model_id},
    )


@event_factory
def WritingJudgeFailed(*, invocation: str, reason: str) -> Event:  # noqa: N802
    return Event(
        signal="writing.judge.failed",
        payload={"invocation": invocation, "reason": reason},
    )


@event_factory
def WritingPersonaRefused(*, reason: str) -> Event:  # noqa: N802
    """Observed when local persona authoring refuses (fail-closed, no fallback).

    reason values: persona_validation_failed, persona_author_failed,
    persona_prompt_missing, persona_model_missing.
    """
    return Event(
        signal="writing.persona.refused",
        role="observation",
        payload={"reason": reason},
    )
