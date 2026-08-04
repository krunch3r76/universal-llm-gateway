"""Contextualize-cache load/store and partial-failure persistence for indexing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from services.rag.chunkers import Chunk
    from services.rag.contextualize_cache import StoredContextRow
    from services.rag.property_index import PropertyIndex

from services.rag.contextualize import (
    CONTEXTUALIZE_PROMPT_HASH,
    compute_neighbor_digest,
)
from services.rag.contextualize_cache import (
    StoredContextRow,
    resolve_source_identity,
)
from services.rag.events.indexing import (
    rag_contextualize_cache_lookup_failed,
    rag_contextualize_cache_store_completed,
    rag_contextualize_cache_store_failed,
)

from .. import state

logger = get_logger(__name__)


async def _store_cached_contexts_best_effort(
    *,
    source: str,
    source_hash: str,
    contextualize_model: str,
    entries: list[StoredContextRow],
    correlation_id: str | None,
    operation: str | None,
) -> None:
    """Persist contextualize cache entries; never propagate failure to caller."""
    source_identity = resolve_source_identity(source)
    if state._property_index is None or not entries or not source_identity:
        return
    try:
        stored = await state._property_index.store_cached_contexts(
            source_identity=source_identity,
            source_hash=source_hash,
            contextualize_model=contextualize_model,
            contextualize_schema_version=CONTEXTUALIZE_PROMPT_HASH,
            entries=entries,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_contextualize_cache_store_completed(
                    file=source,
                    stored=stored,
                    requested=len(entries),
                    contextualize_model=contextualize_model,
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
    except Exception as exc:
        logger.warning(
            "Context cache store failed for %s (index succeeded): %s",
            source,
            exc,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_contextualize_cache_store_failed(
                    file=source,
                    requested=len(entries),
                    contextualize_model=contextualize_model,
                    error=f"{type(exc).__qualname__}: {exc}",
                    operation_id=correlation_id,
                    operation=operation,
                )
            )


async def _load_cached_contexts(
    *,
    source: str,
    source_hash: str,
    chunks: list[Chunk],
    metadatas: list[dict],
    prop_index: PropertyIndex | None,
    context_model: str,
    correlation_id: str,
    operation: str | None,
) -> dict[str, str]:
    """Load per-chunk cached contexts from the property index.

    Returns empty dict if not available or on error (triggering full recompute).
    """
    source_identity = resolve_source_identity(source)
    if prop_index is None or (not source_identity and not source_hash):
        return {}
    chunk_hashes = [str(meta.get("chunk_hash", "")) for meta in metadatas]
    neighbor_digests = {
        chunk_hash: compute_neighbor_digest(chunks, index)
        for index, chunk_hash in enumerate(chunk_hashes)
        if chunk_hash
    }
    try:
        return prop_index.get_cached_contexts(
            source_identity=source_identity,
            source_hash=source_hash,
            chunk_hashes=chunk_hashes,
            neighbor_digests=neighbor_digests,
            contextualize_model=context_model,
            contextualize_schema_version=CONTEXTUALIZE_PROMPT_HASH,
        )
    except Exception as exc:
        logger.warning(
            "Context cache lookup failed for %s; recomputing all: %s", source, exc
        )
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_contextualize_cache_lookup_failed(
                    file=source,
                    requested_chunks=len(chunks),
                    contextualize_model=context_model,
                    error=f"{type(exc).__qualname__}: {exc}",
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        return {}


async def _record_partial_failure(
    *,
    source: str,
    source_hash: str,
    context_model: str,
    correlation_id: str,
    prop_index: PropertyIndex | None,
    ctx_result: object,
    total_chunks: int,
    cache_misses_count: int,
    partial_failed_count: int,
    partial_first_failure: str | None,
) -> tuple[int | None, str | None]:
    """Persist a contextualization partial-failure record to the property index.

    Returns (exception_id, record_error_str). exception_id is None when
    partial_failed_count == 0 or prop_index is unavailable. record_error_str
    is non-None when the persistence call itself raised.
    """
    if partial_failed_count == 0 or prop_index is None:
        return None, None
    try:
        exception_id = await prop_index.record_contextualization_exception(
            source=source,
            source_hash=source_hash,
            contextualize_model=context_model,
            operation_id=correlation_id,
            total_chunks=total_chunks,
            cache_miss_chunks=cache_misses_count,
            successful_chunks=total_chunks - partial_failed_count,
            failed_chunks=partial_failed_count,
            abandoned_indices=(
                ctx_result.abandoned_indices if ctx_result is not None else []
            ),
            request_ids=(ctx_result.request_ids if ctx_result is not None else {}),
            first_failure=partial_first_failure or "",
            idle_seconds=None,
            tail_idle_timeout_s=None,
        )
        return exception_id, None
    except Exception as exc:
        return None, f"{type(exc).__qualname__}: {exc}"
