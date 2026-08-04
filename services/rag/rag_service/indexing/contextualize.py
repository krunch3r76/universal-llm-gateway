"""Contextualization phase for the indexing pipeline."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from services.rag.chunkers import Chunk
    from services.rag.contextualize_cache import StoredContextRow
    from services.rag.property_index import PropertyIndex

from services.rag.contextualize import (
    ContextualizationPhaseError,
    contextualize_chunks,
)
from services.rag.contextualize_cache import (
    StoredContextRow,
    build_context_cache_plan,
    build_stored_context_rows,
    merge_computed_contexts,
)
from services.rag.events.indexing import (
    rag_contextualization_applied,
    rag_contextualization_completed,
    rag_contextualization_exception_record_failed,
    rag_contextualization_exception_recorded,
    rag_contextualization_partial,
    rag_contextualization_started,
    rag_contextualize_cache_evaluated,
)
from services.rag.rag_service.indexing.embed_diff import (
    cache_hit_flags_from_miss_indices,
)

from .. import state
from .contextualize_cache import (
    _load_cached_contexts,
    _record_partial_failure,
)

logger = get_logger(__name__)


async def _run_contextualization_phase(
    *,
    source: str,
    source_hash: str,
    chunks: list[Chunk],
    metadatas: list[dict],
    texts: list[str],
    prop_index: PropertyIndex | None,
    context_model: str,
    context_client_timeout_s: float | None,
    correlation_id: str,
    operation: str | None,
) -> tuple[list[str], list[StoredContextRow], list[bool]]:
    """Run the contextualization phase and return (embed_texts, cache_rows, hit_flags).

    Mutates metadatas in-place to add context_prefix and contextualize_model fields.
    The third element is a per-chunk bool: True when the contextualize cache hit.
    Raises RuntimeError if ALL cache-miss chunks fail (caller should propagate to
    failure recording).
    """
    cached_contexts = await _load_cached_contexts(
        source=source,
        source_hash=source_hash,
        chunks=chunks,
        metadatas=metadatas,
        prop_index=prop_index,
        context_model=context_model,
        correlation_id=correlation_id,
        operation=operation,
    )

    plan = build_context_cache_plan(
        chunks=chunks,
        metadatas=metadatas,
        cached_contexts=cached_contexts,
    )

    if state._event_bus is not None:
        await state._event_bus.publish_nowait(
            rag_contextualize_cache_evaluated(
                file=source,
                total_chunks=len(chunks),
                cache_hits=plan.cache_hits,
                cache_misses=plan.cache_misses_count,
                contextualize_model=context_model,
                operation_id=correlation_id,
                operation=operation,
            )
        )
        await state._event_bus.publish_nowait(
            rag_contextualization_started(
                file=source,
                chunk_count=plan.cache_misses_count,
                model=context_model,
                max_concurrency=32,  # pipeline-controlled; see rag-contextualize-v1.yaml
                operation_id=correlation_id,
                operation=operation,
            )
        )
    context_start = time.monotonic()

    contexts = plan.contexts
    partial_failed_count = 0
    partial_first_failure: str | None = None
    ctx_result = None
    cache_rows_to_store: list[StoredContextRow] = []

    if plan.cache_misses:
        ctx_result = await contextualize_chunks(
            [miss.chunk for miss in plan.cache_misses],
            source,
            context_model,
            pipeline=state._config.contextualize_pipeline if state._config else None,
            client_timeout_s=context_client_timeout_s,
            chunk_indices=[miss.index for miss in plan.cache_misses],
            admission_gate=state._admission_gate,
        )
        computed = ctx_result.contexts
        partial_failed_count = ctx_result.failed_count
        partial_first_failure = ctx_result.first_failure_repr
        contexts = merge_computed_contexts(plan=plan, computed_prefixes=computed)
        # build_stored_context_rows filters out "" entries, so failed chunks
        # remain cache misses for the next reindex attempt.
        cache_rows_to_store = build_stored_context_rows(
            plan=plan, computed_prefixes=computed
        )

    successful_misses = sum(1 for miss in plan.cache_misses if contexts[miss.index])
    (
        contextualization_exception_id,
        contextualization_exception_record_error,
    ) = await _record_partial_failure(
        source=source,
        source_hash=source_hash,
        context_model=context_model,
        correlation_id=correlation_id,
        prop_index=prop_index,
        ctx_result=ctx_result,
        total_chunks=len(chunks),
        cache_misses_count=plan.cache_misses_count,
        partial_failed_count=partial_failed_count,
        partial_first_failure=partial_first_failure,
    )

    if state._event_bus is not None:
        await state._event_bus.publish_nowait(
            rag_contextualization_completed(
                file=source,
                chunk_count=plan.cache_misses_count,
                successful=successful_misses,
                failed=plan.cache_misses_count - successful_misses,
                duration_seconds=time.monotonic() - context_start,
                model=context_model,
                max_concurrency=32,  # pipeline-controlled; see rag-contextualize-v1.yaml
                operation_id=correlation_id,
                operation=operation,
            )
        )
        if 0 < partial_failed_count < plan.cache_misses_count:
            await state._event_bus.publish_nowait(
                rag_contextualization_partial(
                    file=source,
                    total_chunks=len(chunks),
                    failed_chunks=partial_failed_count,
                    successful_chunks=len(chunks) - partial_failed_count,
                    model=context_model,
                    first_failure=partial_first_failure or "",
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        if contextualization_exception_record_error is not None:
            await state._event_bus.publish_nowait(
                rag_contextualization_exception_record_failed(
                    file=source,
                    model=context_model,
                    error=contextualization_exception_record_error,
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        if contextualization_exception_id is not None:
            await state._event_bus.publish_nowait(
                rag_contextualization_exception_recorded(
                    file=source,
                    exception_id=contextualization_exception_id,
                    total_chunks=len(chunks),
                    cache_miss_chunks=plan.cache_misses_count,
                    successful_chunks=len(chunks) - partial_failed_count,
                    failed_chunks=partial_failed_count,
                    abandoned_chunks=(
                        len(ctx_result.abandoned_indices)
                        if ctx_result is not None
                        else 0
                    ),
                    model=context_model,
                    first_failure=partial_first_failure or "",
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        if not (plan.cache_misses and partial_failed_count == plan.cache_misses_count):
            await state._event_bus.publish_nowait(
                rag_contextualization_applied(
                    file=source,
                    chunk_count=len(contexts),
                    model=context_model,
                )
            )

    if plan.cache_misses and partial_failed_count == plan.cache_misses_count:
        assert ctx_result is not None
        raise ContextualizationPhaseError(
            f"All {plan.cache_misses_count} chunks failed contextualization "
            f"for {source} (first: {partial_first_failure}); "
            "indexing_failures row preserved for reconcile retry.",
            first_failure_exc=ctx_result.first_failure_exc,
            failure_category=ctx_result.failure_category,
            failure_reason=ctx_result.failure_reason,
        )

    embed_texts = [
        f"{ctx}\n\n{text}" if ctx else text
        for ctx, text in zip(contexts, texts, strict=True)
    ]
    for i, ctx in enumerate(contexts):
        if ctx:
            metadatas[i]["context_prefix"] = ctx
            metadatas[i]["contextualize_model"] = context_model

    miss_indices = {miss.index for miss in plan.cache_misses}
    cache_hit_flags = cache_hit_flags_from_miss_indices(len(chunks), miss_indices)
    return embed_texts, cache_rows_to_store, cache_hit_flags

