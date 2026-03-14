"""Indexing and deletion pipeline for RAG chunks.

This module owns file-level index/delete operations and their all-or-nothing
coordination with extraction + property index writes. Lifecycle and admin routes
delegate here, while shared mutable resources are read via ``state``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from services.rag.article_registry import (
    get_entry as get_article_entry,
)
from services.rag.article_registry import (
    lookup_article as lookup_article_metadata,
)
from services.rag.chunk_filters import chunk_is_junk
from services.rag.chunkers import Chunk, chunk_file
from services.rag.contextualize import contextualize_chunks
from services.rag.embeddings import embed_chunks
from services.rag.embeddings import get_model_id as get_embed_model_id
from services.rag.events.extraction import rag_extraction_model_mismatch
from services.rag.events.indexing import (
    rag_article_content_hash_mismatch,
    rag_contextualization_applied,
    rag_file_deleted,
    rag_file_indexed,
    rag_file_indexing_failed,
    rag_file_skipped,
    rag_html_normalization_completed,
    rag_html_normalization_failed,
    rag_html_normalization_started,
    rag_property_index_unavailable,
)
from services.rag.extraction_wiring import (
    ExtractionResult,
    recover_missing_extraction,
    run_extraction,
)
from services.rag.indexing_helpers import (
    all_ids_match_prefix,
    check_pdf_duplicate,
    file_hash,
)
from services.rag.models import DeleteResult, IndexResult

from . import state

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # Approximate characters per token for chunk sizing.


async def _index_file(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None = None,
    *,
    chunk_tokens: int | None = None,
    force: bool = False,
) -> IndexResult:
    """Index a file under a per-source lock to avoid watcher/API races."""
    source = str(file_path)
    lock = state._file_index_locks.setdefault(source, asyncio.Lock())
    try:
        async with lock:
            return await _index_file_impl(
                file_path, metadata_overrides, chunk_tokens, source, force=force
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
    """Delete source chunks from ChromaDB and property index."""
    collection = state._get_collection()
    existing = collection.get(where={"source": source}, include=[])
    existing_ids: list[str] = existing.get("ids", [])

    if not existing_ids:
        logger.info("Watcher delete: no chunks found for source=%s", source)
        return DeleteResult(file=source, deleted=0)

    collection.delete(ids=existing_ids)
    if state._property_index is not None:
        for chunk_id in existing_ids:
            await state._property_index.remove_chunk(chunk_id)
        await state._property_index.fts.remove_batch(existing_ids)

    logger.info(
        "Watcher delete complete: source=%s deleted=%d", source, len(existing_ids)
    )
    if state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_file_deleted(file=source, deleted=len(existing_ids))
        )
    return DeleteResult(file=source, deleted=len(existing_ids))


async def _index_file_impl(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    chunk_tokens: int | None,
    source: str,
    *,
    force: bool = False,
) -> IndexResult:
    """Inner implementation of indexing called with source lock held.

    Pending journal invariants:
    - mark_pending executes before any early return.
    - clear_pending executes for every marked file in ``finally``.
    """
    start = time.monotonic()
    is_html_file = file_path.suffix.lower() in {".html", ".htm"}
    raw = file_path.read_bytes()
    if state._config is None:
        raise RuntimeError("RAG service configuration not loaded.")
    schema_version = state._config.knowledge_extraction.schema_version
    content_hash = file_hash(raw, schema_version=schema_version)
    prefix = content_hash[:16]

    if state._registry is not None:
        entry = get_article_entry(state._registry, source)
        if entry and entry.content_hash:
            file_sha = hashlib.sha256(raw).hexdigest()
            if file_sha != entry.content_hash:
                logger.warning(
                    "Article registry content_hash mismatch for %s: expected %s, got %s",
                    source,
                    entry.content_hash,
                    file_sha,
                )
                if state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_article_content_hash_mismatch(
                            file=source,
                            expected_hash=entry.content_hash,
                            actual_hash=file_sha,
                        )
                    )

    collection = state._get_collection()

    if file_path.suffix.lower() == ".pdf":
        dup_result = check_pdf_duplicate(collection, content_hash, source)
        if dup_result is not None:
            if dup_result.duplicate_of is not None:
                logger.info(
                    "PDF duplicate detected: %s is duplicate of %s",
                    source,
                    dup_result.duplicate_of,
                )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_skipped(file=source, reason="duplicate_pdf")
                )
            return dup_result

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    prop_index = state._property_index
    pending_marked = False
    if prop_index is None and state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_property_index_unavailable(file=source)
        )
    if prop_index is not None:
        await prop_index.mark_pending(source)
        pending_marked = True

    try:
        if not force and existing_ids and all_ids_match_prefix(existing_ids, prefix):
            existing_metadatas = [
                m for m in (existing.get("metadatas") or []) if isinstance(m, dict)
            ]
            if state._config is not None and prop_index is not None:
                expected_model = state._config.knowledge_extraction.extraction_model
                has_model_mismatch = bool(expected_model) and any(
                    m.get("extraction_model") != expected_model
                    for m in existing_metadatas
                )
                if has_model_mismatch and state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_extraction_model_mismatch(
                            file=source,
                            expected_model=expected_model,
                            chunk_count=len(existing_ids),
                        )
                    )
                scope = state._config.get_scope_for_path(source)
                ext_result = await recover_missing_extraction(
                    collection=collection,
                    source=source,
                    existing_ids=existing_ids,
                    existing_metadatas=existing_metadatas,
                    config=state._config.knowledge_extraction,
                    property_index=prop_index,
                    event_bus=state._event_bus,
                    scope=scope,
                )
                if ext_result is not None and ext_result.success:
                    await state._maybe_update_corpus_hints()
                    if state._event_bus is not None:
                        await state._event_bus.publish_async_nowait(
                            rag_file_indexed(
                                file=source,
                                deleted=0,
                                indexed=0,
                                duration_seconds=time.monotonic() - start,
                                document_metadata=(
                                    state._article_event_kwargs(state._registry, source)
                                    if state._registry is not None
                                    else None
                                ),
                                processing_seconds=getattr(
                                    ext_result, "processing_seconds", None
                                ),
                                queue_wait_seconds=getattr(
                                    ext_result, "queue_wait_seconds", None
                                ),
                            )
                        )
                    return IndexResult(
                        deleted=0,
                        indexed=0,
                        unchanged=False,
                        file=source,
                        extraction_entities=ext_result.entities,
                        extraction_topics=ext_result.topics,
                    )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_skipped(file=source, reason="unchanged")
                )
            return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

        existing_timestamps: dict[str, str] = {}
        for meta in existing.get("metadatas") or []:
            if isinstance(meta, dict):
                chunk_hash = meta.get("chunk_hash")
                indexed_at = meta.get("indexed_at")
                if isinstance(chunk_hash, str) and isinstance(indexed_at, str):
                    existing_timestamps[chunk_hash] = indexed_at

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
            logger.info(
                "Index complete: file=%s deleted=%d indexed=0",
                source,
                len(existing_ids),
            )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_deleted(file=source, deleted=len(existing_ids))
                )
            return IndexResult(
                deleted=len(existing_ids), indexed=0, unchanged=False, file=source
            )

        texts = [c.text for c in chunks]
        metadatas = [c.metadata for c in chunks]
        ids = [f"{prefix}-{i}" for i in range(len(chunks))]

        merged: dict[str, str | int | float | bool] = {}
        if state._registry is not None:
            entry_meta = lookup_article_metadata(state._registry, source)
            if entry_meta is not None:
                merged.update(entry_meta)
        if metadata_overrides is not None:
            merged.update(metadata_overrides)
        for metadata in metadatas:
            metadata.update(merged)

        now = datetime.now(UTC).isoformat()
        for metadata, chunk in zip(metadatas, chunks, strict=True):
            chunk_hash = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
            metadata["chunk_hash"] = chunk_hash
            metadata["indexed_at"] = existing_timestamps.get(chunk_hash, now)

        if file_path.suffix.lower() == ".pdf":
            for metadata in metadatas:
                metadata["pdf_hash"] = content_hash

        for metadata, chunk in zip(metadatas, chunks, strict=True):
            metadata["is_bibliography"] = chunk_is_junk(chunk.text)

        extraction_entities = 0
        extraction_topics = 0
        extraction_property_entries: list[tuple[str, str, str, str]] = []
        file_batch_start_ts: str | None = None
        ext_result: ExtractionResult | None = None
        if state._config is not None and prop_index is not None:
            scope = state._config.get_scope_for_path(source)
            ext_result = await run_extraction(
                file=source,
                ids=ids,
                chunks=chunks,
                metadatas=metadatas,
                config=state._config.knowledge_extraction,
                property_index=prop_index,
                event_bus=state._event_bus,
                apply_property_index=False,
                scope=scope,
            )
            extraction_entities = ext_result.entities
            extraction_topics = ext_result.topics
            extraction_property_entries = ext_result.property_entries
            ext_batch_start = getattr(ext_result, "batch_start_ts", None)
            file_batch_start_ts = (
                ext_batch_start if isinstance(ext_batch_start, str) else None
            )

            bibliography_ids = {
                ids[i]
                for i, meta in enumerate(metadatas)
                if meta.get("is_bibliography")
            }
            if bibliography_ids:
                extraction_property_entries = [
                    e
                    for e in extraction_property_entries
                    if e[1] not in bibliography_ids
                ]

            if not ext_result.success:
                if state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_file_indexing_failed(
                            file=source,
                            error=(
                                "extraction failed below threshold — "
                                "document excluded until re-indexed"
                            ),
                        )
                    )
                return IndexResult(deleted=0, indexed=0, unchanged=False, file=source)

        embed_texts = texts
        # Contextualization is on by default; set contextualize_model: "" in rag.yaml to disable.
        if state._config is not None and state._config.contextualize_model:
            contexts = await contextualize_chunks(
                chunks,
                source,
                state._config.contextualize_model,
            )
            if state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_contextualization_applied(
                        file=source,
                        chunk_count=len(contexts),
                        model=state._config.contextualize_model,
                    )
                )
            embed_texts = [
                f"{ctx}\n\n{text}" if ctx else text
                for ctx, text in zip(contexts, texts, strict=True)
            ]
            for i, ctx in enumerate(contexts):
                if ctx:
                    metadatas[i]["context_prefix"] = ctx
                    metadatas[i]["contextualize_model"] = (
                        state._config.contextualize_model
                    )

        embeddings = await embed_chunks(embed_texts)
        collection.upsert(
            ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas
        )
        if prop_index is not None:
            await prop_index.fts.insert_batch(
                [(cid, source, text) for cid, text in zip(ids, texts, strict=True)]
            )
        if prop_index is not None and extraction_property_entries:
            try:
                await prop_index.add_batch_with_scope(extraction_property_entries)
            except Exception:
                collection.delete(ids=ids)
                await prop_index.fts.remove_batch(ids)
                raise

        new_id_set = set(ids)
        stale_ids = list(set(existing_ids) - new_id_set)
        if stale_ids:
            if prop_index is not None:
                for old_id in stale_ids:
                    await prop_index.remove_chunk(old_id)
                await prop_index.fts.remove_batch(stale_ids)
            collection.delete(ids=stale_ids)
    except Exception as exc:
        if state._event_bus is not None:
            await state._event_bus.publish_async_nowait(
                rag_file_indexing_failed(
                    file=source,
                    error=f"{type(exc).__qualname__}: {exc}"
                    if str(exc)
                    else type(exc).__qualname__,
                    model=get_embed_model_id(),
                )
            )
        raise
    finally:
        if pending_marked and prop_index is not None:
            await prop_index.clear_pending(source)
        elif pending_marked and prop_index is None:
            logger.error(
                "Property index unavailable while clearing pending for source=%s",
                source,
            )

    await state._maybe_update_corpus_hints()
    logger.info(
        "Index complete: file=%s deleted=%d indexed=%d",
        source,
        len(stale_ids),
        len(chunks),
    )
    if state._event_bus is not None:
        n_bib = sum(1 for m in metadatas if m.get("is_bibliography"))
        await state._event_bus.publish_async_nowait(
            rag_file_indexed(
                file=source,
                deleted=len(stale_ids),
                indexed=len(chunks),
                duration_seconds=time.monotonic() - start,
                batch_start_ts=file_batch_start_ts,
                bibliography_chunks=n_bib,
                document_metadata=(
                    state._article_event_kwargs(state._registry, source)
                    if state._registry is not None
                    else None
                ),
                processing_seconds=getattr(ext_result, "processing_seconds", None),
                queue_wait_seconds=getattr(ext_result, "queue_wait_seconds", None),
            )
        )
    return IndexResult(
        deleted=len(stale_ids),
        indexed=len(chunks),
        unchanged=False,
        file=source,
        extraction_entities=extraction_entities,
        extraction_topics=extraction_topics,
    )
