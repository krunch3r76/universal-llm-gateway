"""Indexing and deletion pipeline for RAG chunks.

This module owns file-level index/delete operations. Extraction is decoupled:
after chunks are embedded and upserted into ChromaDB, the source is enqueued
for async extraction by extraction_worker.py. Files become searchable
immediately — extraction enrichment arrives later.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from chromadb.utils.batch_utils import create_batches

from services.rag.article_registry import (
    get_entry as get_article_entry,
)
from services.rag.chunk_filters import (
    chunk_metadata_is_noise,
    noise_reason,
    normalize_noise_metadata,
)
from services.rag.chunkers import Chunk, chunk_file
from services.rag.contextualize import CONTEXTUALIZE_PROMPT_HASH, contextualize_chunks
from services.rag.contextualize_cache import (
    StoredContextRow,
    build_context_cache_plan,
    build_stored_context_rows,
    merge_computed_contexts,
)
from services.rag.embeddings import embed_chunks, require_healthy
from services.rag.events.articles import (
    rag_article_auto_created,
    rag_article_path_moved,
)
from services.rag.events.indexing import (
    rag_article_content_hash_mismatch,
    rag_chroma_upsert_completed,
    rag_chroma_upsert_started,
    rag_chunk_noise_tagged,
    rag_contextualization_applied,
    rag_contextualization_completed,
    rag_contextualization_started,
    rag_contextualize_cache_evaluated,
    rag_contextualize_cache_lookup_failed,
    rag_contextualize_cache_store_completed,
    rag_contextualize_cache_store_failed,
    rag_embed_completed,
    rag_embed_started,
    rag_file_deleted,
    rag_file_indexed,
    rag_file_indexing_failed,
    rag_file_indexing_failure_cleared,
    rag_file_indexing_failure_recorded,
    rag_file_skipped,
    rag_hints_update_completed,
    rag_hints_update_started,
    rag_html_normalization_completed,
    rag_html_normalization_failed,
    rag_html_normalization_started,
    rag_property_index_unavailable,
    rag_property_write_completed,
    rag_property_write_started,
    rag_source_commit_completed,
    rag_source_commit_started,
)
from services.rag.indexing_helpers import (
    all_ids_match_prefix,
    check_pdf_duplicate,
    file_hash,
)
from services.rag.models import DeleteResult, IndexResult

from . import state

if TYPE_CHECKING:
    from services.rag.config import RagConfig

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4


def _should_skip_cached_source(
    *,
    force: bool,
    operation: str | None,
    cached_source: object | None,
    source_mtime_ns: int,
    source_size_bytes: int,
) -> bool:
    """Return whether the stat-first cache may short-circuit this source.

    With extraction decoupled, the skip check only compares file identity
    (mtime + size). Extraction staleness is handled by the extraction worker.
    """
    if force or operation == "reindex" or cached_source is None:
        return False

    cached = cached_source
    return bool(
        cached.mtime_ns == source_mtime_ns and cached.size_bytes == source_size_bytes
    )


def _classify_indexing_failure(
    exc: BaseException,
    chunk_count: int,
) -> tuple[str, str]:
    """Classify an indexing exception as permanent vs transient.

    Returns (category, reason) where category ∈ {'permanent', 'transient'}.
    stargate-model-lifecycle_ws.mdc authoritative: NOT_IN_CATALOG is structural
    (operator config fix → permanent); PROBE_FAILED is transient.

    NOTE: currently relies on substring matching against exception messages.
    Fragile if upstream wording changes — tracked as Phase 2 deferred tech debt
    (typed domain exceptions from chunking/contextualize/embed/chroma layers).
    """
    exc_type_name = type(exc).__qualname__
    msg = str(exc)
    msg_lower = msg.lower()

    if "max batch size" in msg_lower:
        return ("permanent", "exceeds_chroma_max_batch_size")
    if isinstance(exc, PermissionError):
        return ("permanent", "permission_denied")
    if isinstance(exc, FileNotFoundError):
        return ("permanent", "file_not_found")
    if "embedding dimension" in msg_lower:
        return ("permanent", "embedding_dimension_mismatch")
    if "unsupported file type" in msg_lower or exc_type_name == "UnsupportedFileError":
        return ("permanent", "unsupported_file_type")
    if "NOT_IN_CATALOG" in msg:
        return ("permanent", "contextualize_model_not_in_catalog")

    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return ("transient", "timeout")
    if "PROBE_FAILED" in msg:
        return ("transient", "contextualize_probe_failed")
    if "capacity" in msg_lower or "REQUEST_TIMEOUT" in msg:
        return ("transient", "gateway_capacity")
    if "Session is closed" in msg or "ConnectionError" in exc_type_name:
        return ("transient", "event_service_disconnected")

    return ("transient", "unclassified")


async def _record_indexing_failure_best_effort(
    *,
    exc: BaseException,
    source: str,
    source_hash: str | None,
    source_size_bytes: int | None,
    source_mtime_ns: int | None,
    chunk_count: int,
) -> None:
    """Persist failure row and emit recorded event; never mask original exc."""
    if state._property_index is None:
        return
    try:
        category, reason = _classify_indexing_failure(exc, chunk_count=chunk_count)
        attempt_count = await state._property_index.record_indexing_failure(
            source=source,
            failure_category=category,
            failure_reason=reason,
            error_message=str(exc) or type(exc).__qualname__,
            error_type=type(exc).__qualname__,
            source_hash=source_hash,
            source_size_bytes=source_size_bytes,
            source_mtime_ns=source_mtime_ns,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_file_indexing_failure_recorded(
                    file=source,
                    failure_category=category,
                    failure_reason=reason,
                    attempt_count=attempt_count,
                )
            )
    except Exception as record_exc:
        logger.error(
            "failed to persist indexing failure for %s: %s", source, record_exc
        )


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
    if state._property_index is None or not entries or not source_hash:
        return
    try:
        stored = await state._property_index.store_cached_contexts(
            source_hash=source_hash,
            contextualize_model=contextualize_model,
            contextualize_schema_version=CONTEXTUALIZE_PROMPT_HASH,
            entries=entries,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
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
            await state._event_bus.publish_async_nowait(
                rag_contextualize_cache_store_failed(
                    file=source,
                    requested=len(entries),
                    contextualize_model=contextualize_model,
                    error=f"{type(exc).__qualname__}: {exc}",
                    operation_id=correlation_id,
                    operation=operation,
                )
            )


def _derive_subdirectory(source: str, config: RagConfig) -> str:
    """Return the parent path of source relative to its configured watch root."""
    source_path = Path(source).expanduser().resolve()
    for watch_directory in config.watch_directories:
        watch_root = Path(watch_directory.path).expanduser().resolve()
        try:
            relative = source_path.relative_to(watch_root)
        except ValueError:
            continue
        return str(relative.parent) if relative.parent != Path(".") else ""
    return ""


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
    """Index a file under a per-source lock to avoid watcher/API races."""
    source = str(file_path)
    lock = state._file_index_locks.setdefault(source, asyncio.Lock())
    try:
        async with lock:
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
        if state._file_index_locks.get(source) is lock:
            state._file_index_locks.pop(source, None)


async def _delete_file(file_path: Path) -> DeleteResult:
    """Delete all indexed chunks for a removed file under per-source lock."""
    source = str(file_path)
    lock = state._file_index_locks.setdefault(source, asyncio.Lock())
    try:
        async with lock:
            return await _delete_file_impl(source)
    finally:
        if state._file_index_locks.get(source) is lock:
            state._file_index_locks.pop(source, None)


async def _delete_file_impl(source: str) -> DeleteResult:
    """Delete source chunks and source-scoped metadata for a removed file."""
    collection = state._get_collection()
    existing = collection.get(where={"source": source}, include=[])
    existing_ids: list[str] = existing.get("ids", [])

    if existing_ids:
        collection.delete(ids=existing_ids)
    else:
        logger.info(
            "Watcher delete: no chunks found for source=%s; clearing metadata only",
            source,
        )

    if state._property_index is not None:
        await state._property_index.remove_source_metadata(
            source,
            existing_ids if existing_ids else None,
            remove_article=False,
        )

    if state._property_index is not None:
        cleared = await state._property_index.clear_indexing_failure(source)
        if cleared and state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_file_indexing_failure_cleared(file=source, reason="source_deleted")
            )

    deleted = len(existing_ids)
    logger.info("Watcher delete complete: source=%s deleted=%d", source, deleted)
    if existing_ids and state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_file_deleted(file=source, deleted=deleted)
        )
    return DeleteResult(file=source, deleted=deleted)


async def _enqueue_for_extraction(source: str) -> None:
    """Queue a source for async extraction if the scope allows it."""
    if state._config is None or state._property_index is None:
        return
    scope = state._config.get_scope_for_path(source)
    if state._config.knowledge_extraction.should_extract_scope(scope):
        await state._property_index.enqueue_extraction(source)


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
            await event_bus.publish_async_nowait(
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
            await event_bus.publish_async_nowait(
                rag_chroma_upsert_completed(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(b_ids),
                    operation=operation,
                    batch_index=batch_index,
                    batch_total=batch_total,
                )
            )


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
                await state._event_bus.publish_async_nowait(
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

    if prop_index is not None:
        orphan = prop_index.find_orphaned_article_by_hash(
            content_hash=source_hash,
            new_source_path=source,
        )
        if orphan is not None:
            new_scope = state._config.get_scope_for_path(source)
            new_subdirectory = _derive_subdirectory(source, state._config)
            moved = await prop_index.move_article_source_path(
                old_source_path=orphan["source_path"],
                new_source_path=source,
                new_filename=file_path.name,
                new_scope=new_scope,
                new_subdirectory=new_subdirectory,
            )
            if moved:
                state.refresh_article_registry_from_row(
                    prop_index.get_article_row(source),
                )
                if state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_article_path_moved(
                            old_path=orphan["source_path"],
                            new_path=source,
                            content_hash=source_hash,
                        )
                    )

    if state._registry is not None:
        entry = get_article_entry(state._registry, source)
        if entry and entry.content_hash:
            if source_hash != entry.content_hash:
                logger.warning(
                    "Article registry content_hash mismatch for %s: expected %s, got %s",
                    source,
                    entry.content_hash,
                    source_hash,
                )
                if state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_article_content_hash_mismatch(
                            file=source,
                            expected_hash=entry.content_hash,
                            actual_hash=source_hash,
                        )
                    )

    collection = state._get_collection()

    if file_path.suffix.lower() == ".pdf":
        dup_result = check_pdf_duplicate(collection, source_hash, source)
        if dup_result is not None:
            if dup_result.duplicate_of is not None:
                logger.info(
                    "PDF duplicate detected: %s is duplicate of %s",
                    source,
                    dup_result.duplicate_of,
                )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_skipped(
                        file=source,
                        reason="duplicate_pdf",
                        operation_id=correlation_id,
                        operation=operation,
                    )
                )
            return dup_result

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    if prop_index is None and state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_property_index_unavailable(file=source)
        )

    try:
        if not force and existing_ids and all_ids_match_prefix(existing_ids, prefix):
            if prop_index is not None:
                await prop_index.upsert_indexed_source(
                    source=source,
                    mtime_ns=source_stat.st_mtime_ns,
                    size_bytes=source_stat.st_size,
                    extraction_schema_version=schema_version,
                    extraction_model=extraction_model,
                    source_hash=source_hash,
                )
                if not prop_index.article_exists(source):
                    scope = state._config.get_scope_for_path(source)
                    subdirectory = _derive_subdirectory(source, state._config)
                    created = await prop_index.sync_article_structural_fields(
                        source_path=source,
                        filename=file_path.name,
                        content_hash=source_hash,
                        scope=scope,
                        subdirectory=subdirectory,
                    )
                    if created and state._event_bus is not None:
                        await state._event_bus.publish_async_nowait(
                            rag_article_auto_created(
                                source_path=source,
                                content_hash=source_hash,
                                scope=scope,
                            )
                        )
                await _enqueue_for_extraction(source)
            if emit_skip_event and state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_skipped(
                        file=source,
                        reason="unchanged",
                        operation_id=correlation_id,
                        operation=operation,
                    )
                )
            return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

        existing_timestamps: dict[str, str] = {
            meta["chunk_hash"]: meta["indexed_at"]
            for meta in existing.get("metadatas", [])
            if isinstance(meta, dict)
            and isinstance(meta.get("chunk_hash"), str)
            and isinstance(meta.get("indexed_at"), str)
        }

        target_chars = chunk_tokens * _CHARS_PER_TOKEN if chunk_tokens else None
        if is_html_file and state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_html_normalization_started(file=source)
            )
        try:
            chunks: list[Chunk] = chunk_file(file_path, target_chars=target_chars)
        except Exception as exc:
            if is_html_file and state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_html_normalization_failed(file=source, error=str(exc))
                )
            raise
        if is_html_file and state._event_bus is not None:
            total_chars = sum(len(c.text) for c in chunks)
            await state._event_bus.publish_async_nowait(
                rag_html_normalization_completed(file=source, output_chars=total_chars)
            )
        if not chunks:
            if existing_ids:
                if prop_index is not None:
                    for old_id in existing_ids:
                        await prop_index.remove_chunk(old_id)
                    await prop_index.fts.remove_batch(existing_ids)
                collection.delete(ids=existing_ids)
            if prop_index is not None:
                await prop_index.upsert_indexed_source(
                    source=source,
                    mtime_ns=source_stat.st_mtime_ns,
                    size_bytes=source_stat.st_size,
                    extraction_schema_version=schema_version,
                    extraction_model=extraction_model,
                    source_hash=source_hash,
                )
            logger.info(
                "Index complete: file=%s deleted=%d indexed=0",
                source,
                len(existing_ids),
            )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_deleted(
                        file=source,
                        deleted=len(existing_ids),
                        operation_id=correlation_id,
                        operation=operation,
                    )
                )
            return IndexResult(
                deleted=len(existing_ids), indexed=0, unchanged=False, file=source
            )

        texts = [c.text for c in chunks]
        metadatas = [c.metadata for c in chunks]
        ids = [f"{prefix}-{i}" for i in range(len(chunks))]

        if metadata_overrides is not None:
            for metadata in metadatas:
                metadata.update(metadata_overrides)

        now = datetime.now(UTC).isoformat()
        for chunk_index, (metadata, chunk) in enumerate(
            zip(metadatas, chunks, strict=True)
        ):
            # Positional prefix: same text at different positions must hash
            # differently so contextualize cache keys don't collide when
            # neighbor context differs (Task 3.0 invariant).
            positional_material = f"{chunk_index}|{chunk.text}".encode()
            chunk_hash = hashlib.sha256(positional_material).hexdigest()[:16]
            metadata["chunk_hash"] = chunk_hash
            metadata["indexed_at"] = existing_timestamps.get(chunk_hash, now)

        for metadata in metadatas:
            metadata["source_hash"] = source_hash

        for metadata, chunk in zip(metadatas, chunks, strict=True):
            nr = noise_reason(chunk.text)
            if nr is not None:
                metadata["is_noise"] = True
                metadata["noise_reason"] = nr
            else:
                metadata["is_noise"] = False
            normalize_noise_metadata(metadata)

        if state._event_bus is not None:
            for cid, meta in zip(ids, metadatas, strict=True):
                if chunk_metadata_is_noise(meta):
                    await state._event_bus.publish_async_nowait(
                        rag_chunk_noise_tagged(
                            chunk_id=cid,
                            source=source,
                            noise_reason=meta.get("noise_reason", "unspecified_noise"),
                        )
                    )

        embed_texts = texts
        cache_rows_to_store: list[StoredContextRow] = []
        if state._config is not None and state._config.contextualize_model:
            context_model = state._config.contextualize_model
            context_max_concurrency = state._config.contextualize_max_concurrency
            context_client_timeout_s = state._config.contextualize_client_timeout_s
            context_timeout_s = min(30.0, context_client_timeout_s)

            cached_contexts: dict[str, str] = {}
            if prop_index is not None and source_hash:
                try:
                    cached_contexts = prop_index.get_cached_contexts(
                        source_hash=source_hash,
                        chunk_hashes=[
                            str(meta.get("chunk_hash", "")) for meta in metadatas
                        ],
                        contextualize_model=context_model,
                        contextualize_schema_version=CONTEXTUALIZE_PROMPT_HASH,
                    )
                except Exception as exc:
                    logger.warning(
                        "Context cache lookup failed for %s; recomputing all: %s",
                        source,
                        exc,
                    )
                    if state._event_bus is not None:
                        await state._event_bus.publish_async_nowait(
                            rag_contextualize_cache_lookup_failed(
                                file=source,
                                requested_chunks=len(chunks),
                                contextualize_model=context_model,
                                error=f"{type(exc).__qualname__}: {exc}",
                                operation_id=correlation_id,
                                operation=operation,
                            )
                        )
                    cached_contexts = {}

            plan = build_context_cache_plan(
                chunks=chunks,
                metadatas=metadatas,
                cached_contexts=cached_contexts,
            )

            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
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
                await state._event_bus.publish_async_nowait(
                    rag_contextualization_started(
                        file=source,
                        chunk_count=plan.cache_misses_count,
                        model=context_model,
                        max_concurrency=context_max_concurrency,
                        operation_id=correlation_id,
                        operation=operation,
                    )
                )
            context_start = time.monotonic()

            contexts = plan.contexts
            if plan.cache_misses:
                computed = await contextualize_chunks(
                    [miss.chunk for miss in plan.cache_misses],
                    source,
                    context_model,
                    timeout_s=context_timeout_s,
                    max_concurrency=context_max_concurrency,
                    client_timeout_s=context_client_timeout_s,
                    probe_timeout_s=state._config.ctx_probe_timeout_s,
                    probe_backoff_initial_s=state._config.ctx_probe_backoff_initial_s,
                    probe_backoff_max_s=state._config.ctx_probe_backoff_max_s,
                    probe_max_probes=state._config.ctx_probe_max_probes,
                    global_gate=state._global_contextualize_gate,
                )
                contexts = merge_computed_contexts(
                    plan=plan,
                    computed_prefixes=computed,
                )
                cache_rows_to_store = build_stored_context_rows(
                    plan=plan,
                    computed_prefixes=computed,
                )

            successful_misses = sum(
                1 for miss in plan.cache_misses if contexts[miss.index]
            )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_contextualization_completed(
                        file=source,
                        chunk_count=plan.cache_misses_count,
                        successful=successful_misses,
                        failed=plan.cache_misses_count - successful_misses,
                        duration_seconds=time.monotonic() - context_start,
                        model=context_model,
                        max_concurrency=context_max_concurrency,
                        operation_id=correlation_id,
                        operation=operation,
                    )
                )
                await state._event_bus.publish_async_nowait(
                    rag_contextualization_applied(
                        file=source,
                        chunk_count=len(contexts),
                        model=context_model,
                    )
                )
            embed_texts = [
                f"{ctx}\n\n{text}" if ctx else text
                for ctx, text in zip(contexts, texts, strict=True)
            ]
            for i, ctx in enumerate(contexts):
                if ctx:
                    metadatas[i]["context_prefix"] = ctx
                    metadatas[i]["contextualize_model"] = context_model

        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_embed_started(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(embed_texts),
                    operation=operation,
                )
            )
        embeddings = await embed_chunks(embed_texts)
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_embed_completed(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(embed_texts),
                    operation=operation,
                )
            )
        await _upsert_chroma_chunk_batches(
            chroma_client=state._chroma,
            collection=collection,
            event_bus=state._event_bus,
            source=source,
            correlation_id=correlation_id,
            operation=operation,
            ids=ids,
            embeddings=embeddings,
            texts=texts,
            metadatas=metadatas,
        )
        if prop_index is not None:
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_property_write_started(
                        file=source,
                        operation_id=correlation_id,
                        chunk_count=len(ids),
                        property_entries=0,
                        operation=operation,
                    )
                )
            await prop_index.fts.insert_batch(
                [(cid, source, text) for cid, text in zip(ids, texts, strict=True)]
            )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_property_write_completed(
                        file=source,
                        operation_id=correlation_id,
                        chunk_count=len(ids),
                        property_entries=0,
                        operation=operation,
                    )
                )

        new_id_set = set(ids)
        stale_ids = list(set(existing_ids) - new_id_set)
    except Exception as exc:
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_file_indexing_failed(
                    file=source,
                    error=f"{type(exc).__qualname__}: {exc}"
                    if str(exc)
                    else type(exc).__qualname__,
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

    try:
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
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
            scope = state._config.get_scope_for_path(source)
            subdirectory = _derive_subdirectory(source, state._config)
            created = await prop_index.sync_article_structural_fields(
                source_path=source,
                filename=file_path.name,
                content_hash=source_hash,
                scope=scope,
                subdirectory=subdirectory,
            )
            if created and state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
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
            await state._event_bus.publish_async_nowait(
                rag_source_commit_completed(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(chunks),
                    stale_chunks=len(stale_ids),
                    operation=operation,
                )
            )
            await state._event_bus.publish_async_nowait(
                rag_hints_update_started(
                    file=source,
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
        await state._maybe_update_corpus_hints()
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_hints_update_completed(
                    file=source,
                    operation_id=correlation_id,
                    operation=operation,
                )
            )
    except Exception as exc:
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_file_indexing_failed(
                    file=source,
                    error=f"{type(exc).__qualname__}: {exc}"
                    if str(exc)
                    else type(exc).__qualname__,
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

    if state._property_index is not None:
        cleared = await state._property_index.clear_indexing_failure(source)
        if cleared and state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_file_indexing_failure_cleared(
                    file=source, reason="indexed_successfully"
                )
            )

    await _enqueue_for_extraction(source)

    logger.info(
        "Index complete: file=%s deleted=%d indexed=%d",
        source,
        len(stale_ids),
        len(chunks),
    )
    if state._event_bus is not None:
        n_noise = sum(1 for m in metadatas if chunk_metadata_is_noise(m))
        await state._event_bus.publish_async_nowait(
            rag_file_indexed(
                file=source,
                deleted=len(stale_ids),
                indexed=len(chunks),
                duration_seconds=time.monotonic() - start,
                noise_chunks=n_noise,
                document_metadata=(
                    state._article_event_kwargs(state._registry, source)
                    if state._registry is not None
                    else None
                ),
                operation_id=correlation_id,
                operation=operation,
            )
        )
    return IndexResult(
        deleted=len(stale_ids),
        indexed=len(chunks),
        unchanged=False,
        file=source,
    )
