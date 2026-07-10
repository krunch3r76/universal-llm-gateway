"""Indexing and deletion pipeline for RAG chunks.

This module owns file-level index/delete operations. Extraction is decoupled:
after chunks are embedded and upserted into ChromaDB, the source is enqueued
for async extraction by extraction_worker.py. Files become searchable
immediately — extraction enrichment arrives later.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from uuid import uuid4

from universal_logging import get_logger

from services.rag.chunkers import chunk_file
from services.rag.embeddings import require_healthy
from services.rag.events.indexing import (
    rag_file_indexing_failed,
    rag_file_indexing_gated,
    rag_file_skipped,
    rag_html_normalization_completed,
    rag_html_normalization_failed,
    rag_html_normalization_started,
    rag_property_index_unavailable,
)
from services.rag.indexing_helpers import file_hash
from services.rag.models import IndexResult

from .. import state
from .article_sync import _run_article_sync_phase
from .commit import _run_commit_phase
from .delete import (
    _delete_file as _delete_file,
)
from .embed import _run_embed_phase
from .failure_ops import (
    _record_indexing_failure_best_effort,
)
from .file_guards import (
    _handle_empty_chunks,
    _handle_pdf_duplicate_or_move,
    _handle_unchanged_prefix_skip,
)
from .finalize import _finalize_index_success
from .source_identity import (
    _derive_subdirectory,
    _should_skip_cached_source,
)

logger = get_logger(__name__)

_CHARS_PER_TOKEN = 4


async def _index_file(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None = None,
    *,
    chunk_tokens: int | None = None,
    force: bool = False,
    emit_skip_event: bool = True,
    operation_id: str | None = None,
    operation: str | None = None,
) -> IndexResult:
    """Index a file under a per-source gate to avoid watcher/API races."""
    from ..source_path_gate import acquire_source_path, release_source_path

    source = str(file_path.resolve())
    await acquire_source_path(source)
    try:
        return await _index_file_impl(
            file_path,
            metadata_overrides,
            chunk_tokens,
            source,
            force=force,
            emit_skip_event=emit_skip_event,
            operation_id=operation_id,
            operation=operation,
        )
    finally:
        await release_source_path(source)


async def _index_file_impl(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    chunk_tokens: int | None,
    source: str,
    *,
    force: bool = False,
    emit_skip_event: bool = True,
    operation_id: str | None = None,
    operation: str | None = None,
) -> IndexResult:
    """Inner implementation of indexing called with source lock held.

    Extraction is decoupled: after successful ChromaDB upsert, the source
    is enqueued for async extraction. No extraction calls on this path.
    """
    start = time.monotonic()
    correlation_id = operation_id or uuid4().hex
    is_html_file = file_path.suffix.lower() in {".html", ".htm"}
    if state._config is None:
        raise RuntimeError("RAG service configuration not loaded.")

    prop_index = state._property_index
    schema_version = state._config.knowledge_extraction.schema_version
    extraction_model = state._config.knowledge_extraction.extraction_model
    source_stat = await asyncio.to_thread(file_path.stat)

    # Layer 2 — authoritative entity-admission gate (thread 1136 A1). Every
    # entry path (inotify, reconcile, initial sweep, admin reindex) funnels
    # through _index_file_impl, so "no backing entity ⇒ not indexed" holds as a
    # true invariant for an entity-gated root, not only for sweeps. Orthogonal
    # to `force` (force re-chunks unchanged content; it does not override
    # backing). Fail-closed + self-healing: an unknown / not-yet-loaded admitted
    # set holds (skip) and the reconcile loop re-attempts once the gate
    # refreshes. This is NOT a failure (no indexing_failures row); the gated
    # signal is coordination, not failure. Sweeps short-circuit at Layer 1
    # before reaching here, so a sweep-skipped file is never double-emitted.
    entity_gate = state._entity_admission_gate
    if (
        entity_gate is not None
        and state._config.is_path_entity_gated(source)
        and not entity_gate.is_admitted(source)
    ):
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_file_indexing_gated(file=source, layer="index_funnel")
            )
        return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

    if prop_index is not None:
        cached_source = prop_index.get_indexed_source(source)
        if _should_skip_cached_source(
            force=force,
            operation=operation,
            cached_source=cached_source,
            source_mtime_ns=source_stat.st_mtime_ns,
            source_size_bytes=source_stat.st_size,
        ):
            await prop_index.clear_pending(source)
            if emit_skip_event and state._event_bus is not None:
                await state._event_bus.publish_nowait(
                    rag_file_skipped(
                        file=source,
                        reason="unchanged",
                        operation_id=correlation_id,
                        operation=operation,
                    )
                )
            return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

    await require_healthy()
    raw = await asyncio.to_thread(file_path.read_bytes)
    content_hash = file_hash(raw, schema_version=schema_version)
    prefix = content_hash[:16]
    source_hash = hashlib.sha256(raw).hexdigest()

    article_sync_old_path = await _run_article_sync_phase(
        source=source,
        source_hash=source_hash,
        file_path=file_path,
        prop_index=prop_index,
        config=state._config,
        event_bus=state._event_bus,
        registry=state._registry,
        refresh_article_registry_from_row=state.refresh_article_registry_from_row,
    )

    collection = state._get_collection()

    dup_or_skip = await _handle_pdf_duplicate_or_move(
        file_path=file_path,
        source=source,
        source_hash=source_hash,
        collection=collection,
        article_sync_old_path=article_sync_old_path,
        force=force,
        correlation_id=correlation_id,
        operation=operation,
    )
    if dup_or_skip is not None:
        return dup_or_skip

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    if prop_index is None and state._event_bus is not None:
        await state._event_bus.publish_nowait(
            rag_property_index_unavailable(file=source)
        )

    try:
        prefix_skip = await _handle_unchanged_prefix_skip(
            source=source,
            file_path=file_path,
            existing_ids=existing_ids,
            prefix=prefix,
            prop_index=prop_index,
            source_stat=source_stat,
            source_hash=source_hash,
            schema_version=schema_version,
            extraction_model=extraction_model,
            force=force,
            emit_skip_event=emit_skip_event,
            correlation_id=correlation_id,
            operation=operation,
        )
        if prefix_skip is not None:
            return prefix_skip

        existing_timestamps: dict[str, str] = {
            meta["chunk_hash"]: meta["indexed_at"]
            for meta in existing.get("metadatas", [])
            if isinstance(meta, dict)
            and isinstance(meta.get("chunk_hash"), str)
            and isinstance(meta.get("indexed_at"), str)
        }

        target_chars = chunk_tokens * _CHARS_PER_TOKEN if chunk_tokens else None
        if is_html_file and state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_html_normalization_started(file=source)
            )
        try:
            chunks = chunk_file(file_path, target_chars=target_chars)
        except Exception as exc:
            if is_html_file and state._event_bus is not None:
                await state._event_bus.publish_nowait(
                    rag_html_normalization_failed(file=source, error=str(exc))
                )
            raise
        if is_html_file and state._event_bus is not None:
            total_chars = sum(len(c.text) for c in chunks)
            await state._event_bus.publish_nowait(
                rag_html_normalization_completed(file=source, output_chars=total_chars)
            )
        if not chunks:
            return await _handle_empty_chunks(
                source=source,
                existing_ids=existing_ids,
                prop_index=prop_index,
                collection=collection,
                source_stat=source_stat,
                source_hash=source_hash,
                schema_version=schema_version,
                extraction_model=extraction_model,
                correlation_id=correlation_id,
                operation=operation,
            )

        embed_result = await _run_embed_phase(
            file_path=file_path,
            source=source,
            source_hash=source_hash,
            chunks=chunks,
            existing_ids=existing_ids,
            existing_timestamps=existing_timestamps,
            metadata_overrides=metadata_overrides,
            prop_index=prop_index,
            collection=collection,
            chroma_client=state._chroma,
            event_bus=state._event_bus,
            config=state._config,
            correlation_id=correlation_id,
            operation=operation,
            prefix=prefix,
        )
        chunks = embed_result.chunks
        ids = embed_result.ids
        metadatas = embed_result.metadatas
        stale_ids = embed_result.stale_ids
        cache_rows_to_store = embed_result.cache_rows_to_store

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
            chunk_count=len(locals().get("chunks", []) or []),
        )
        raise

    scope = state._config.get_scope_for_path(source)
    subdirectory = _derive_subdirectory(source, state._config)
    await _run_commit_phase(
        source=source,
        file_path=file_path,
        prop_index=prop_index,
        collection=collection,
        correlation_id=correlation_id,
        chunks=chunks,
        stale_ids=stale_ids,
        metadatas=metadatas,
        ids=ids,
        source_hash=source_hash,
        source_stat=source_stat,
        schema_version=schema_version,
        extraction_model=extraction_model,
        cache_rows_to_store=cache_rows_to_store,
        subdirectory=subdirectory,
        scope=scope,
        operation=operation,
        operation_id=correlation_id,
    )

    return await _finalize_index_success(
        source=source,
        chunks=chunks,
        stale_ids=stale_ids,
        metadatas=metadatas,
        start=start,
        correlation_id=correlation_id,
        operation=operation,
    )
