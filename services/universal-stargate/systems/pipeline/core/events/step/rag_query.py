"""RAG query analysis and rewrite bus event factories.

Callers: rag_query_retrieve handler (rag_context_v1). Covers scope analysis
output, query rewrite completion, and rewrite skip. Signals in namespace
pipeline.rag.query.*.
"""

from universal_event_bus import Event, event_factory


@event_factory
def RagQueryAnalysisCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    needs_retrieval: bool,
    scope: str,
    scope_confidence: float,
    out_of_scope_reason: str,
) -> Event:
    """Emitted when scope analysis output is consumed by retrieval.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        needs_retrieval: Whether retrieval should be attempted
        scope: Predicted scope label
        scope_confidence: Model confidence in predicted scope
        out_of_scope_reason: Empty when in-scope, else explanatory text
    """
    return Event(
        signal="pipeline.rag.query.analysis.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "needs_retrieval": needs_retrieval,
            "scope": scope,
            "scope_confidence": scope_confidence,
            "out_of_scope_reason": out_of_scope_reason,
        },
    )


@event_factory
def RagQueryRewriteCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    rewrite_count: int,
    hyde_present: bool,
) -> Event:
    """Emitted when rewrite output is available for retrieval.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        rewrite_count: Number of rewritten queries produced
        hyde_present: Whether HyDE passage is non-empty
    """
    return Event(
        signal="pipeline.rag.query.rewrite.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "rewrite_count": rewrite_count,
            "hyde_present": hyde_present,
        },
    )


@event_factory
def RagQueryRewriteSkipped(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    reason: str,
) -> Event:
    """Emitted when rewrite generation is intentionally not used.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step identifier
        reason: Skip reason code
    """
    return Event(
        signal="pipeline.rag.query.rewrite.skipped",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "reason": reason,
        },
    )
