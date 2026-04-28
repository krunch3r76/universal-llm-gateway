"""Commit phase and ChromaDB upsert helpers for the indexing pipeline."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.rag.chunkers import Chunk
    from services.rag.contextualize_cache import StoredContextRow
    from services.rag.property_index import PropertyIndex

import chromadb
from chromadb.utils.batch_utils import create_batches

from services.rag.events.articles import rag_article_auto_created
from services.rag.events.indexing import (
    rag_chroma_upsert_completed,
    rag_chroma_upsert_started,
    rag_file_indexing_failed,
    rag_hints_update_completed,
    rag_hints_update_started,
    rag_source_commit_completed,
    rag_source_commit_started,
)
from services.rag.rag_service._indexing_contextualize import (
    _store_cached_contexts_best_effort,
)
from services.rag.rag_service._indexing_failure_ops import (
    _record_indexing_failure_best_effort,
)

from . import state

logger = logging.getLogger(__name__)


async def _upsert_chroma_chunk_batches(
    *,
    chroma_client: Any,
    collection: Any,
    event_bus: Any,
    source: str,
    correlation_id: str,
    operation: str | None,
    ids: list[str],
    embeddings: Any,
    texts: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    """Upsert chunk rows in ChromaDB-sized batches (backend max_batch_size)."""
    if chroma_client is None:
        raise RuntimeError("ChromaDB client not initialized")
    batches = create_batches(
        chroma_client,
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=texts,
    )
    batch_total = len(batches)
    for batch_index, (b_ids, b_embeddings, b_metadatas, b_documents) in enumerate(
        batches
    ):
        if event_bus is not None:
            await event_bus.publish_nowait(
                rag_chroma_upsert_started(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(b_ids),
                    operation=operation,
                    batch_index=batch_index,
                    batch_total=batch_total,
                )
            )
        collection.upsert(
            ids=b_ids,
            embeddings=b_embeddings,
            documents=b_documents,
            metadatas=b_metadatas,
        )
        if event_bus is not None:
            await event_bus.publish_nowait(
                rag_chroma_upsert_completed(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(b_ids),
                    operation=operation,
                    batch_index=batch_index,
                    batch_total=batch_total,
                )
            )


async def _run_commit_phase(
    *,
    source: str,
    file_path: Path,
    prop_index: PropertyIndex | None,
    collection: chromadb.Collection,
    correlation_id: str,
    chunks: list[Chunk],
    stale_ids: list[str],
    metadatas: list[dict],
    ids: list[str],
    source_hash: str,
    source_stat: os.stat_result,
    schema_version: str,
    extraction_model: str,
    cache_rows_to_store: list[StoredContextRow],
    subdirectory: str,
    scope: str,
    operation: str | None,
    operation_id: str | None,
) -> None:
    """Run the commit phase (stale cleanup + property writes + hints).

    Raises on failure after emitting a rag.file.indexing.failed event and
    persisting a failure row. The caller is responsible for the post-commit
    success path (clear failure, enqueue, final events, return).
    """
    try:
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_source_commit_started(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(chunks),
                    stale_chunks=len(stale_ids),
                    operation=operation,
                )
            )
        if stale_ids:
            if prop_index is not None:
                for old_id in stale_ids:
                    await prop_index.remove_chunk(old_id)
                await prop_index.fts.remove_batch(stale_ids)
            collection.delete(ids=stale_ids)
        if prop_index is not None:
            await prop_index.upsert_indexed_source(
                source=source,
                mtime_ns=source_stat.st_mtime_ns,
                size_bytes=source_stat.st_size,
                extraction_schema_version=schema_version,
                extraction_model=extraction_model,
                source_hash=source_hash,
            )
            created = await prop_index.sync_article_structural_fields(
                source_path=source,
                filename=file_path.name,
                content_hash=source_hash,
                scope=scope,
                subdirectory=subdirectory,
            )
            if created and state._event_bus is not None:
                await state._event_bus.publish_nowait(
                    rag_article_auto_created(
                        source_path=source,
                        content_hash=source_hash,
                        scope=scope,
                    )
                )
            if (
                cache_rows_to_store
                and source_hash
                and state._config is not None
                and state._config.contextualize_model
            ):
                await _store_cached_contexts_best_effort(
                    source=source,
                    source_hash=source_hash,
                    contextualize_model=state._config.contextualize_model,
                    entries=cache_rows_to_store,
                    correlation_id=correlation_id,
                    operation=operation,
                )
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_source_commit_completed(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(chunks),
                    stale_chunks=len(stale_ids),
                    operation=operation,
                )
            )
            await state._event_bus.publish_nowait(
                rag_hints_update_started(
                    file=source,
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        await state._maybe_update_corpus_hints()
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_hints_update_completed(
                    file=source,
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
    except Exception as exc:
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_file_indexing_failed(
                    file=source,
                    error=(
                        f"{type(exc).__qualname__}: {exc}"
                        if str(exc)
                        else type(exc).__qualname__
                    ),
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        await _record_indexing_failure_best_effort(
            exc=exc,
            source=source,
            source_hash=source_hash,
            source_size_bytes=source_stat.st_size,
            source_mtime_ns=source_stat.st_mtime_ns,
            chunk_count=len(chunks),
        )
        raise
