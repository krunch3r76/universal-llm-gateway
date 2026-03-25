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
from typing import TYPE_CHECKING

from services.rag.article_registry import (
    get_entry as get_article_entry,
)
from services.rag.chunk_filters import (
    chunk_metadata_is_noise,
    noise_reason,
    normalize_noise_metadata,
)
from services.rag.chunkers import Chunk, chunk_file
from services.rag.contextualize import contextualize_chunks
from services.rag.embeddings import embed_chunks, require_healthy
from services.rag.events.articles import (
    rag_article_auto_created,
    rag_article_path_moved,
)
from services.rag.events.extraction import rag_extraction_model_mismatch
from services.rag.events.indexing import (
    rag_article_content_hash_mismatch,
    rag_chunk_noise_tagged,
    rag_contextualization_applied,
    rag_file_deleted,
    rag_file_indexed,
    rag_file_indexing_failed,
    rag_file_retry_deferred,
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

if TYPE_CHECKING:
    from services.rag.config import RagConfig

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # Approximate characters per token for chunk sizing, used for chunk sizing heuristics.


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
            )
    finally:
        if state._file_index_locks.get(source) is lock:
            state._file_index_locks.pop(source, None)
        # Consider if a separate cleanup for prop_index.clear_pending is needed here
        # if _index_file_impl fails before its own finally block can execute it.


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

    deleted = len(existing_ids)
    logger.info("Watcher delete complete: source=%s deleted=%d", source, deleted)
    if existing_ids and state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_file_deleted(file=source, deleted=deleted)
        )
    return DeleteResult(file=source, deleted=deleted)


async def _index_file_impl(
    file_path: Path,
    metadata_overrides: dict[str, str | int | float | bool] | None,
    chunk_tokens: int | None,
    source: str,
    *,
    force: bool = False,
    emit_skip_event: bool = True,
) -> IndexResult:
    """Inner implementation of indexing called with source lock held.

    Pending journal invariants:
    - stat-only unchanged skips do not mark pending.
    - mark_pending executes before recovery or any mutating index path.
    - clear_pending executes for every marked file in ``finally``.
    """
    start = time.monotonic()
    is_html_file = file_path.suffix.lower() in {".html", ".htm"}
    if state._config is None:
        raise RuntimeError("RAG service configuration not loaded.")

    prop_index = state._property_index
    schema_version = state._config.knowledge_extraction.schema_version
    extraction_model = state._config.knowledge_extraction.extraction_model
    source_stat = await asyncio.to_thread(file_path.stat)

    if not force and prop_index is not None:
        cached_source = prop_index.get_indexed_source(source)
        if (
            cached_source is not None
            and cached_source.mtime_ns == source_stat.st_mtime_ns
            and cached_source.size_bytes == source_stat.st_size
            and cached_source.extraction_schema_version == schema_version
            and cached_source.extraction_model == extraction_model
            and not prop_index.has_retriable_failures(source)
        ):
            await prop_index.clear_pending(source)
            if emit_skip_event and state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_skipped(file=source, reason="unchanged")
                )
            return IndexResult(deleted=0, indexed=0, unchanged=True, file=source)

    await require_healthy()
    raw = await asyncio.to_thread(file_path.read_bytes)
    # Decide if source_hash should also incorporate schema_version
    # If so:
    # source_hash = file_hash(raw, schema_version=schema_version)
    # If not, ensure content_hash and source_hash are clearly distinct concepts.
    # For now, assuming content_hash is the primary content identifier.
    content_hash = file_hash(raw, schema_version=schema_version)
    prefix = content_hash[:16]
    source_hash = hashlib.sha256(
        raw
    ).hexdigest()  # This hash is independent of schema_version

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
                    rag_file_skipped(file=source, reason="duplicate_pdf")
                )
            return dup_result

    existing = collection.get(where={"source": source}, include=["metadatas"])
    existing_ids: list[str] = existing.get("ids", [])

    pending_marked = False
    if prop_index is None and state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_property_index_unavailable(file=source)
        )

    try:
        if not force and existing_ids and all_ids_match_prefix(existing_ids, prefix):
            existing_metadatas = [
                m for m in (existing.get("metadatas") or []) if isinstance(m, dict)
            ]
            if prop_index is not None:
                expected_model = state._config.knowledge_extraction.extraction_model
                mismatch_chunks = [
                    m
                    for m in existing_metadatas
                    if not chunk_metadata_is_noise(m)
                    and bool(expected_model)
                    and m.get("extraction_model") != expected_model
                ]
                if mismatch_chunks and state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_extraction_model_mismatch(
                            file=source,
                            expected_model=expected_model,
                            chunk_count=len(mismatch_chunks),
                        )
                    )

                scope = state._config.get_scope_for_path(source)
                await prop_index.mark_pending(source)
                pending_marked = True
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
                    # Invariant: upsert_indexed_source only after chunks exist
                    # in ChromaDB. ¬write on extraction failure paths.
                    await prop_index.upsert_indexed_source(
                        source=source,
                        mtime_ns=source_stat.st_mtime_ns,
                        size_bytes=source_stat.st_size,
                        extraction_schema_version=schema_version,
                        extraction_model=extraction_model,
                    )
                    await state._maybe_update_corpus_hints()
                    if state._event_bus is not None:
                        await state._event_bus.publish_async_nowait(
                            rag_file_indexed(
                                file=source,
                                deleted=0,
                                indexed=0,
                                duration_seconds=time.monotonic() - start,
                                batch_start_ts=getattr(
                                    ext_result, "batch_start_ts", None
                                ),
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

            if prop_index is not None:
                await prop_index.upsert_indexed_source(
                    source=source,
                    mtime_ns=source_stat.st_mtime_ns,
                    size_bytes=source_stat.st_size,
                    extraction_schema_version=schema_version,
                    extraction_model=extraction_model,
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
            if emit_skip_event and state._event_bus is not None:
                await state._event_bus.publish_async_nowait(
                    rag_file_skipped(file=source, reason="unchanged")
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
                )
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

        if metadata_overrides is not None:
            for metadata in metadatas:
                metadata.update(metadata_overrides)

        now = datetime.now(UTC).isoformat()
        for metadata, chunk in zip(metadatas, chunks, strict=True):
            chunk_hash = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
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

        extraction_entities: int | None = None
        extraction_topics: int | None = None
        extraction_property_entries: list[tuple[str, str, str, str]] = []
        file_batch_start_ts: str | None = None
        ext_result: ExtractionResult | None = None
        if state._config is not None and prop_index is not None:
            scope = state._config.get_scope_for_path(source)
            extract_indices = [
                i for i, m in enumerate(metadatas) if not chunk_metadata_is_noise(m)
            ]
            extract_ids = [ids[i] for i in extract_indices]
            extract_chunks = [chunks[i] for i in extract_indices]
            extract_metadatas = [metadatas[i] for i in extract_indices]
            if extract_ids:
                ext_result = await run_extraction(
                    file=source,
                    ids=extract_ids,
                    chunks=extract_chunks,
                    metadatas=extract_metadatas,
                    config=state._config.knowledge_extraction,
                    property_index=prop_index,
                    event_bus=state._event_bus,
                    apply_property_index=False,
                    scope=scope,
                )
            else:
                ext_result = ExtractionResult(success=True)
            extraction_entities = ext_result.entities
            extraction_topics = ext_result.topics
            extraction_property_entries = ext_result.property_entries
            ext_batch_start = getattr(ext_result, "batch_start_ts", None)
            file_batch_start_ts = (
                ext_batch_start if isinstance(ext_batch_start, str) else None
            )

            noise_ids = {
                ids[i]
                for i, meta in enumerate(metadatas)
                if chunk_metadata_is_noise(meta)
            }
            if noise_ids:
                extraction_property_entries = [
                    e for e in extraction_property_entries if e[1] not in noise_ids
                ]

            if not ext_result.success:
                if state._event_bus is not None:
                    await state._event_bus.publish_async_nowait(
                        rag_file_retry_deferred(
                            file=source,
                            reason="extraction_incomplete",
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
                )
            )
        raise
    finally:
        if pending_marked:
            if prop_index is not None:
                await prop_index.clear_pending(source)
            else:
                # This case should ideally not happen if mark_pending was called
                # only when prop_index was available.
                logger.error(
                    "Property index became unavailable after marking pending for source=%s",
                    source,
                )

    if prop_index is not None:
        await prop_index.upsert_indexed_source(
            source=source,
            mtime_ns=source_stat.st_mtime_ns,
            size_bytes=source_stat.st_size,
            extraction_schema_version=schema_version,
            extraction_model=extraction_model,
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

    await state._maybe_update_corpus_hints()
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
                batch_start_ts=file_batch_start_ts,
                noise_chunks=n_noise,
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
