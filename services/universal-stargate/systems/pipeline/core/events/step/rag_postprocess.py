"""RAG post-retrieval bus event factories (expansion, boost, rerank, hints, context).

Callers: rag_query_retrieve, rag_rerank_assemble, filter_hints, and
refine_generation_context handlers (rag_context_v1). Covers all post-RRF
processing phases: neighbor expansion, coverage selection, metadata boost,
LLM reranking, hint filtering, and generation context refinement.
Signals in namespace pipeline.rag.*.
"""

from universal_event_bus import Event, event_factory


@event_factory
def RagNeighborExpansionApplied(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    enabled: bool,
    neighbors_added: int,
    neighbors_fetched: int,
    sources_expanded: int,
    expansion_n: int,
    max_chunks: int,
    expansion_seconds: float,
) -> Event:
    """Emitted after neighbor chunk expansion is applied.

    Emitted only when neighbor expansion is enabled, including zero-addition runs.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        enabled: Whether expansion was enabled
        neighbors_added: Count of accepted neighbor chunks
        neighbors_fetched: Count fetched from RAG chunks_by_index endpoint
        sources_expanded: Distinct source files expanded
        expansion_n: ±N neighbor window
        max_chunks: Maximum chunks allowed after expansion
        expansion_seconds: Wall-clock expansion duration
    """
    return Event(
        signal="pipeline.rag.neighbor.expansion.applied",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "enabled": enabled,
            "neighbors_added": neighbors_added,
            "neighbors_fetched": neighbors_fetched,
            "sources_expanded": sources_expanded,
            "expansion_n": expansion_n,
            "max_chunks": max_chunks,
            "expansion_seconds": expansion_seconds,
        },
    )


@event_factory
def RagCoverageSelectionApplied(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    enabled: bool,
    applied: bool,
    chunks_before: int,
    chunks_after: int,
) -> Event:
    """Emitted after coverage-aware selection runs in metadata boost phase.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        enabled: Whether coverage selection was enabled in effective options
        applied: Whether metadata boost phase executed (False if skipped)
        chunks_before: Chunk count before coverage-aware selection
        chunks_after: Chunk count after coverage-aware selection
    """
    return Event(
        signal="pipeline.rag.coverage.selection.applied",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "enabled": enabled,
            "applied": applied,
            "chunks_before": chunks_before,
            "chunks_after": chunks_after,
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
def RagHintsFiltered(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    query_terms: list[str],
    original_hint_count: int,
    filtered_hint_count: int,
    filtered_hints: list[str],
    fallback: bool,
    scoring_mode: str = "chunk_weighted",
    min_threshold: int = 2,
    capped: bool = False,
    cap_limit: int = 0,
) -> Event:
    """Emitted after corpus hints are filtered by co-occurrence with query terms.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        query_terms: Terms from suggest_terms used for co-occurrence lookup
        original_hint_count: Total corpus hints before filtering
        filtered_hint_count: Hints remaining after filtering
        filtered_hints: The filtered hint terms
        fallback: True if filtering produced no results and all hints were kept
        scoring_mode: Co-occurrence scoring strategy ("chunk_weighted" or "doc_level")
        min_threshold: Minimum co-occurrence count required to keep a hint
        capped: True if max_hints cap was applied after filtering
        cap_limit: max_hints value (0 = no cap configured)
    """
    return Event(
        signal="pipeline.rag.hints.filtered",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "query_terms": query_terms,
            "original_hint_count": original_hint_count,
            "filtered_hint_count": filtered_hint_count,
            "filtered_hints": filtered_hints,
            "fallback": fallback,
            "scoring_mode": scoring_mode,
            "min_threshold": min_threshold,
            "capped": capped,
            "cap_limit": cap_limit,
        },
    )


@event_factory
def RagGenerationContextRefined(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    predicted_scopes: list[str],
    original_must_include: list[str],
    enriched_must_include: list[str],
    scope_anchors_added: list[str],
    flat_hint_count: int,
    register_scopes_included: int,
    register_scopes_total: int,
) -> Event:
    """Emitted after generation context is refined with scope-filtered vocabulary.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        predicted_scopes: Scopes from analyze_scope used for filtering
        original_must_include: must_include before enrichment
        enriched_must_include: must_include after adding scope anchors
        scope_anchors_added: Anchor terms added by enrichment
        flat_hint_count: Co-occurrence-filtered flat hints count
        register_scopes_included: Number of scopes in the filtered register vocabulary
        register_scopes_total: Total scopes in the unfiltered register vocabulary
    """
    return Event(
        signal="pipeline.rag.generation.context.refined",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "predicted_scopes": predicted_scopes,
            "original_must_include": original_must_include,
            "enriched_must_include": enriched_must_include,
            "scope_anchors_added": scope_anchors_added,
            "flat_hint_count": flat_hint_count,
            "register_scopes_included": register_scopes_included,
            "register_scopes_total": register_scopes_total,
        },
    )
