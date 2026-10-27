"""Domain events for writer-specialist semantic_judge."""

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
