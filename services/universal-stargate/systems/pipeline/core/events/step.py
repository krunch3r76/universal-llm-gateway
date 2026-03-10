"""Step lifecycle events."""

from typing import Any

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
def StepModelResolved(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    resolved_model_id: str,
    selection_source: str,
) -> Event:
    """
    Emitted immediately after model selection, before inference begins.

    Corrects the static default carried by StepStarted with the actual model
    that will be invoked (post profile/requirements resolution).

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        resolved_model_id: Concrete model ID selected for this invocation
        selection_source: How the model was chosen (e.g. "runtime_override",
                          "intelligence_profile", "model_ref")
    """
    return Event(
        signal="pipeline.step.model.resolved",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "model_id": resolved_model_id,
            "selection_source": selection_source,
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
    model_id: str | None = None,
    exit_code: int | None = None,
    json_output_keys: list[str] | None = None,
) -> Event:
    """Emitted when step completes successfully.

    model_id: the resolved model actually invoked (overrides StepStarted's
              static models.yaml default; None for non-generate steps).
    Optional exit_code: populated for shell_v1 steps (non-None even on rc=0).
    Enables event consumers to detect non-zero shell exits that produced output.
    Optional json_output_keys: top-level keys of JSON output (observability).
    """
    payload: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "step_name": step_name,
        "duration_seconds": duration_seconds,
        "output_length": output_length,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model_call_count": model_call_count,
    }
    if model_id is not None:
        payload["model_id"] = model_id
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
    traceback: str | None = None,
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
        traceback: Full Python traceback (omitted when None)
    """
    payload: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "step_name": step_name,
        "duration_seconds": duration_seconds,
        "error": error,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model_call_count": model_call_count,
    }
    if traceback is not None:
        payload["traceback"] = traceback
    return Event(
        signal="pipeline.step.failed",
        payload=payload,
    )


@event_factory
def StepContextExceeded(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    model_id: str,
    estimated_tokens: int,
    context_length: int,
    effective_context_per_slot: int,
    prompt_chars: int,
) -> Event:
    """Emitted when estimated prompt tokens exceed the model's context window.

    Pre-flight heuristic check (chars/4).  Accompanies the recorder-only
    ContextExceeded event with a canonical bus signal for live debugging.
    """
    return Event(
        signal="pipeline.step.context.exceeded",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "model_id": model_id,
            "estimated_tokens": estimated_tokens,
            "context_length": context_length,
            "effective_context_per_slot": effective_context_per_slot,
            "prompt_chars": prompt_chars,
        },
    )


@event_factory
def StepModelFallback(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    primary_model: str,
    fallback_model: str,
    primary_error_type: str,
    fallback_attempt: int,
    total_fallbacks: int,
    succeeded: bool,
) -> Event:
    """Emitted when step-level model fallback is attempted or resolves.

    Fires at the executor level after the full retry chain exhausts
    for the primary model. Covers all failure types: timeout, proxy error,
    handler error.
    """
    return Event(
        signal="pipeline.step.model.fallback",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "primary_error_type": primary_error_type,
            "fallback_attempt": fallback_attempt,
            "total_fallbacks": total_fallbacks,
            "succeeded": succeeded,
        },
    )


@event_factory
def StepModelFallbackSuppressed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    primary_error_type: str,
    suppression_reason: str,
) -> Event:
    """Emitted when step-level fallback is intentionally not attempted."""
    return Event(
        signal="pipeline.step.model.fallback.suppressed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "primary_error_type": primary_error_type,
            "suppression_reason": suppression_reason,
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
def RagRetrievalParamsResolved(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    consumer_model: str | None,
    consumer_tier: str | None,
    profile_class: str | None,
    max_chunks: int,
    top_k_per_query: int,
    rrf_k: int,
    scope: str,
    retrieval_mode: str,
    uses_explicit_prefixes: bool,
) -> Event:
    """Emitted by rag_multi_retrieve_v1 after effective retrieval params are resolved.

    Payload:
        consumer_model: Model that will read the retrieved context (None if not set)
        consumer_tier: Tier of the consumer model (None if not set)
        profile_class: Matched model_class name (e.g. "frontier"), None if exact profile
                       or no profile matched
        max_chunks: Effective rag_max_chunks after profile + runtime merge
        top_k_per_query: Effective rag_top_k_per_query
        rrf_k: Effective RRF constant
        scope: Resolved retrieval scope (research / project / both / custom)
        retrieval_mode: "scope" or "source_prefixes"
        uses_explicit_prefixes: True iff caller passed rag_source_prefixes
    """
    return Event(
        signal="pipeline.rag.retrieval.params.resolved",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "consumer_model": consumer_model,
            "consumer_tier": consumer_tier,
            "profile_class": profile_class,
            "max_chunks": max_chunks,
            "top_k_per_query": top_k_per_query,
            "rrf_k": rrf_k,
            "scope": scope,
            "retrieval_mode": retrieval_mode,
            "uses_explicit_prefixes": uses_explicit_prefixes,
        },
    )


@event_factory
def RagRetrievalCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    predicted_scope: str,
    scope_confidence: float,
    fallback_triggered: bool,
    chunks_per_query: list[int],
    zero_result_queries: int,
    rrf_score_min: float,
    rrf_score_max: float,
    rrf_score_mean: float,
    chunks_after_merge: int,
    total_retrieval_seconds: float,
) -> Event:
    """Emitted after successful RAG multi-query retrieval + RRF merge.

    Captures scope prediction accuracy signals and retrieval quality metrics.
    Paired with RagRetrievalParamsResolved (pre-retrieval) to give full lifecycle.

    Payload:
        predicted_scope: Scope label from the rewrite model (before fallback)
        scope_confidence: Model's confidence in its scope prediction (0.0-1.0)
        fallback_triggered: True if scope was overridden due to low confidence
        chunks_per_query: Per-query chunk counts (length = successful query count)
        zero_result_queries: Count of queries that returned 0 chunks
        rrf_score_min: Minimum RRF score in merged result set
        rrf_score_max: Maximum RRF score in merged result set
        rrf_score_mean: Mean RRF score in merged result set
        chunks_after_merge: Final chunk count after RRF deduplication
        total_retrieval_seconds: Wall-clock time for all queries + merge
    """
    return Event(
        signal="pipeline.rag.retrieval.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "predicted_scope": predicted_scope,
            "scope_confidence": scope_confidence,
            "fallback_triggered": fallback_triggered,
            "chunks_per_query": chunks_per_query,
            "zero_result_queries": zero_result_queries,
            "rrf_score_min": rrf_score_min,
            "rrf_score_max": rrf_score_max,
            "rrf_score_mean": rrf_score_mean,
            "chunks_after_merge": chunks_after_merge,
            "total_retrieval_seconds": total_retrieval_seconds,
        },
    )


@event_factory
def RagRetrievalFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    error: str,
    total_retrieval_seconds: float,
) -> Event:
    """Emitted when all RAG queries fail (no results to merge).

    Payload:
        error: Description of the failure
        total_retrieval_seconds: Wall-clock time before failure determination
    """
    return Event(
        signal="pipeline.rag.retrieval.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "error": error,
            "total_retrieval_seconds": total_retrieval_seconds,
        },
    )


@event_factory
def RagRetrievalSkipped(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    reason: str,
    out_of_scope_reason: str,
) -> Event:
    """Emitted when retrieval is skipped due to out-of-scope detection.

    Fires instead of retrieval.completed/failed when the rewrite model
    determined the query is unanswerable from the active corpus and no
    user-supplied source_prefixes override is present.

    Payload:
        reason: Skip category ("out_of_scope" is the only current value)
        out_of_scope_reason: Rewrite model's explanation of the corpus mismatch
    """
    return Event(
        signal="pipeline.rag.retrieval.skipped",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "reason": reason,
            "out_of_scope_reason": out_of_scope_reason,
        },
    )


@event_factory
def RagMetadataBoostApplied(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    metadata_hit_count: int,
    avg_metadata_score: float,
    applied: bool,
    chunks_after_boost: int,
) -> Event:
    """Emitted after post-RRF metadata boost is applied (or skipped).

    Payload:
        metadata_hit_count: Chunks with non-zero metadata overlap score
        avg_metadata_score: Mean raw metadata score across all input chunks
        applied: True if boost was enabled and had query terms to match
        chunks_after_boost: Final chunk count after boost + optional coverage selection
    """
    return Event(
        signal="pipeline.rag.metadata.boost.applied",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "metadata_hit_count": metadata_hit_count,
            "avg_metadata_score": avg_metadata_score,
            "applied": applied,
            "chunks_after_boost": chunks_after_boost,
        },
    )


@event_factory
def RagRerankCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    rerank_enabled: bool,
    model_id: str | None,
    chunks_input: int,
    chunks_output: int,
    windows_evaluated: int,
    max_rank_movement_observed: int,
    total_rerank_seconds: float,
) -> Event:
    """Emitted after LLM reranking completes (or is skipped when disabled).

    Payload:
        rerank_enabled: True if LLM reranking was performed
        model_id: Model used for reranking (None if skipped)
        chunks_input: Number of candidate chunks considered for reranking
        chunks_output: Final chunk count after reranking
        windows_evaluated: Number of sliding windows processed by LLM
        max_rank_movement_observed: Largest rank position change in this execution
        total_rerank_seconds: Wall-clock time for the reranking phase
    """
    return Event(
        signal="pipeline.rag.rerank.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "rerank_enabled": rerank_enabled,
            "model_id": model_id,
            "chunks_input": chunks_input,
            "chunks_output": chunks_output,
            "windows_evaluated": windows_evaluated,
            "max_rank_movement_observed": max_rank_movement_observed,
            "total_rerank_seconds": total_rerank_seconds,
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


@event_factory
def SubPipelineExpanded(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    parent_step_name: str,
    resolved_output_step: str,
    expanded_step_count: int,
) -> Event:
    """Emitted when a ``sub_pipeline`` step is expanded into namespaced steps."""
    return Event(
        signal="pipeline.subpipeline.expanded",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "parent_step_name": parent_step_name,
            "resolved_output_step": resolved_output_step,
            "expanded_step_count": expanded_step_count,
        },
    )
