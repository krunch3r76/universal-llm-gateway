"""Step lifecycle events."""

from universal_event_bus import Event, event_factory


@event_factory
def StepStarted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    step_type: str,
    model_id: str | None,
    is_map_step: bool,
) -> Event:
    """
    Emitted when step execution begins (includes both regular and map steps).

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        step_type: Step type (e.g., "generate", "filter")
        model_id: Target model identifier (None if not applicable)
        is_map_step: True if step uses map execution mode
    """
    return Event(
        signal="pipeline.step.started",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "step_type": step_type,
            "model_id": model_id,
            "is_map_step": is_map_step,
        },
    )


@event_factory
def StepCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    duration_seconds: float,
    output_length: int,
    prompt_tokens: int,
    completion_tokens: int,
    model_call_count: int,
    exit_code: int | None = None,
    json_output_keys: list[str] | None = None,
) -> Event:
    """Emitted when step completes successfully.

    Optional exit_code: populated for shell_v1 steps (non-None even on rc=0).
    Enables event consumers to detect non-zero shell exits that produced output.
    Optional json_output_keys: top-level keys of JSON output (observability).
    """
    payload: dict = {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "step_name": step_name,
        "duration_seconds": duration_seconds,
        "output_length": output_length,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model_call_count": model_call_count,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if json_output_keys is not None:
        payload["json_output_keys"] = json_output_keys
    return Event(
        signal="pipeline.step.completed",
        payload=payload,
    )


@event_factory
def StepFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    duration_seconds: float | None,
    error: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model_call_count: int = 0,
) -> Event:
    """
    Emitted when step execution fails.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        duration_seconds: Time until failure (None if failed before execution)
        error: Error message
        prompt_tokens: Prompt tokens consumed before failure
        completion_tokens: Completion tokens consumed before failure
        model_call_count: Total model calls attempted before failure
    """
    return Event(
        signal="pipeline.step.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "duration_seconds": duration_seconds,
            "error": error,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "model_call_count": model_call_count,
        },
    )


@event_factory
def StepSkipped(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    reason: str,
) -> Event:
    """
    Emitted when step is skipped due to condition evaluation.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        reason: Why step was skipped
    """
    return Event(
        signal="pipeline.step.skipped",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "reason": reason,
        },
    )


@event_factory
def CoverageAuditCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    total_facts: int,
    covered_count: int,
    uncovered_count: int,
    mean_score: float,
    coverage_pct: float,
    threshold: float,
) -> Event:
    """
    Emitted after embedding-based fact coverage audit completes.

    Payload:
        total_facts: Verified facts checked against the answer
        covered_count: Facts with best-sentence similarity >= threshold
        uncovered_count: Facts below the threshold
        mean_score: Mean of per-fact best-sentence similarity scores
        coverage_pct: covered_count / total_facts * 100
        threshold: Similarity threshold used
    """
    return Event(
        signal="pipeline.consensus.coverage.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "total_facts": total_facts,
            "covered_count": covered_count,
            "uncovered_count": uncovered_count,
            "mean_score": mean_score,
            "coverage_pct": coverage_pct,
            "threshold": threshold,
        },
    )


@event_factory
def OrganizeFactsCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    total_facts: int,
    sections_created: int,
    facts_assigned: int,
    valid_json: bool,
) -> Event:
    """
    Emitted after organize_facts generates and validates an outline.

    Payload:
        total_facts: Verified facts provided to organize_facts
        sections_created: Number of sections produced in the outline
        facts_assigned: Unique fact indices assigned across all sections
        valid_json: True when outline JSON is valid and assignment-complete
    """
    return Event(
        signal="pipeline.consensus.organize.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "total_facts": total_facts,
            "sections_created": sections_created,
            "facts_assigned": facts_assigned,
            "valid_json": valid_json,
        },
    )


@event_factory
def CombinePassagesCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    fact_count: int,
    chunk_count: int,
    cited_count: int,
    uncited_count: int,
    coverage_pct: float,
) -> Event:
    """
    Emitted after verified facts are synthesised into a combined answer.

    Payload:
        fact_count: Total verified facts sent to combine
        chunk_count: Number of synthesis chunks (1 = bootstrap only)
        cited_count: Unique fact indices cited at least once in the output
        uncited_count: Fact indices with no citation in the output
        coverage_pct: cited_count / fact_count * 100
    """
    return Event(
        signal="pipeline.consensus.combine.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "fact_count": fact_count,
            "chunk_count": chunk_count,
            "cited_count": cited_count,
            "uncited_count": uncited_count,
            "coverage_pct": coverage_pct,
        },
    )


@event_factory
def StepConditionEvaluated(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    condition: str,
    result: bool,
    available_outputs: list[str],
) -> Event:
    """Emitted when a step's condition expression is evaluated."""
    return Event(
        signal="pipeline.step.condition.evaluated",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "condition": condition,
            "result": result,
            "available_outputs": available_outputs,
        },
    )
