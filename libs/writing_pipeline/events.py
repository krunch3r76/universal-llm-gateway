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
def WritingPersonaRefused(  # noqa: N802
    *,
    reason: str,
    flagged_candidates: list[str] | None = None,
    judged_invented: list[str] | None = None,
    persona_judgments: list[dict[str, str]] | None = None,
    detail: str | None = None,
) -> Event:
    """Observed when local persona authoring refuses (fail-closed, no fallback).

    reason values: persona_validation_failed, persona_judge_unavailable,
    persona_judge_invalid, persona_author_failed, persona_prompt_missing,
    persona_model_missing.
    """
    payload: dict[str, object] = {"reason": reason}
    if flagged_candidates is not None:
        payload["flagged_candidates"] = flagged_candidates
    if judged_invented is not None:
        payload["judged_invented"] = judged_invented
    if persona_judgments is not None:
        payload["persona_judgments"] = persona_judgments
    if detail is not None:
        payload["detail"] = detail
    return Event(
        signal="writing.persona.refused",
        role="observation",
        payload=payload,
    )
