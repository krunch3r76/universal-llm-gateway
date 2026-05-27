"""RAG retrieval lifecycle bus event factories: scope, params, retrieval outcomes.

Callers: rag_query_retrieve handler (rag_context_v1). Covers the full retrieval
lifecycle from scope rejection through params resolution, retrieval complete/fail/skip,
and post-RRF junk/diversity filters. Signals in namespace pipeline.rag.retrieval.*
and pipeline.rag.scope.*.
"""

from universal_event_bus import Event, event_factory


@event_factory
def RagScopeRejected(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    reason: str,
    scope: str | list[str],
    details: str = "",
) -> Event:
    """Emitted when scope validation rejects the scope; retrieval returns 0 chunks.

    Fired before params resolution — mutually exclusive with the
    ``params.resolved`` → ``completed``/``failed`` path.

    Reasons:
        invalid_scope_override — explicit scope_override contains unknown scope(s)
        invalid_predicted_scope — rewrite model predicted a scope not in the catalog
        scope_confidence_below_threshold — confidence below configured threshold
        scope_catalog_unavailable — RAG /scopes endpoint unreachable (fail-closed)

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        reason: Rejection reason code
        scope: Rejected scope value (string or list)
        details: Human-readable rejection detail
    """
    return Event(
        signal="pipeline.rag.scope.rejected",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "reason": reason,
            "scope": scope,
            "details": details,
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
    scope: str | list[str],
    retrieval_mode: str,
    uses_explicit_prefixes: bool,
    pool_b_enabled: bool = True,
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
        scope: Resolved retrieval scope: single label or list of labels for multiscope
               (research / project / both / custom or list thereof)
        retrieval_mode: "scope" or "source_prefixes"
        uses_explicit_prefixes: True iff caller passed rag_source_prefixes
        pool_b_enabled: True when Pool B (sparse facet / IDF) runs for this step
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
            "pool_b_enabled": pool_b_enabled,
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
    neighbor_expansion_added: int = 0,
    coverage_bias_applied: bool = False,
    coverage_bias_query_class: str = "default",
    coverage_bias_anchor_source: str | None = None,
    coverage_bias_boosted_chunks: int = 0,
) -> Event:
    """Emitted after successful RAG multi-query retrieval + RRF merge.

    Captures scope prediction accuracy signals and retrieval quality metrics.
    Paired with RagRetrievalParamsResolved (pre-retrieval) to give full lifecycle.

    Payload:
        predicted_scope: Scope label from the rewrite model (before alias resolution)
        scope_confidence: Model's confidence in its scope prediction (0.0-1.0)
        fallback_triggered: True if scope was normalized via alias resolution
                            (no broad fallback exists — invalid/low-confidence
                            scopes are rejected before retrieval)
        chunks_per_query: Per-query chunk counts (length = successful query count)
        zero_result_queries: Count of queries that returned 0 chunks
        rrf_score_min: Minimum RRF score in merged result set
        rrf_score_max: Maximum RRF score in merged result set
        rrf_score_mean: Mean RRF score in merged result set
        chunks_after_merge: Final chunk count after RRF deduplication
        total_retrieval_seconds: Wall-clock time for all queries + merge
        neighbor_expansion_added: Number of chunks added by neighbor expansion
                                  (0 when expansion is disabled or adds none)
        coverage_bias_applied: True when enumeration-style query coverage bias ran
        coverage_bias_query_class: ``default`` or ``enumeration`` from classifier
        coverage_bias_anchor_source: Dominant source used for section boosting, if any
        coverage_bias_boosted_chunks: Count of chunks whose scores were boosted
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
            "neighbor_expansion_added": neighbor_expansion_added,
            "coverage_bias_applied": coverage_bias_applied,
            "coverage_bias_query_class": coverage_bias_query_class,
            "coverage_bias_anchor_source": coverage_bias_anchor_source,
            "coverage_bias_boosted_chunks": coverage_bias_boosted_chunks,
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
def RagRetrievalBibliographyFiltered(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    chunks_dropped: int,
) -> Event:
    """Emitted when post-RRF junk filter removes bibliography-heavy chunks.

    Payload:
        chunks_dropped: Number of chunks removed by the junk/bibliography filter
    """
    return Event(
        signal="pipeline.rag.retrieval.bibliography.filtered",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "chunks_dropped": chunks_dropped,
        },
    )


@event_factory
def RagRetrievalSourceDiversityLimited(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    per_source_limit: int,
    chunks_dropped: int,
    chunks_before: int,
    chunks_after: int,
) -> Event:
    """Emitted when source-diversity cap removes chunks from a dominant source.

    Payload:
        per_source_limit: Configured max chunks allowed per source document
        chunks_dropped: Number of chunks removed by source-diversity enforcement
        chunks_before: Chunk count before applying source-diversity cap
        chunks_after: Chunk count after applying source-diversity cap
    """
    return Event(
        signal="pipeline.rag.retrieval.diversity.limited",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "per_source_limit": per_source_limit,
            "chunks_dropped": chunks_dropped,
            "chunks_before": chunks_before,
            "chunks_after": chunks_after,
        },
    )
