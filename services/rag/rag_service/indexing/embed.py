"""Embed phase: chunk preparation, noise tagging, contextualization, Chroma upsert, FTS."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from services.rag.chunk_filters import (
    chunk_metadata_is_noise,
    noise_reason,
    normalize_noise_metadata,
)
from services.rag.chunkers import Chunk
from services.rag.embeddings import embed_chunks
from services.rag.events.indexing import (
    rag_chunk_noise_tagged,
    rag_embed_completed,
    rag_embed_diff_evaluated,
    rag_embed_started,
    rag_property_write_completed,
    rag_property_write_started,
)

from .chroma import _upsert_chroma_chunk_batches
from .contextualize import (
    _run_contextualization_phase,
)
from .embed_diff import (
    compose_chunk_id,
    compute_chunk_hash,
    compute_path_key,
    compute_stale_ids,
    partition_embed_work,
    subset_by_indices,
)

if TYPE_CHECKING:
    import chromadb
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

logger = get_logger(__name__)


@dataclass
class EmbedPhaseResult:
    chunks: list[Chunk]
    ids: list[str]
    texts: list[str]
    metadatas: list[dict]
    cache_rows_to_store: list = field(default_factory=list)
    stale_ids: list[str] = field(default_factory=list)


async def _run_embed_phase(
    *,
    file_path: Path,
    source: str,
    source_hash: str,
    chunks: list[Chunk],
    existing_ids: list[str],
    existing_timestamps: dict[str, str],
    metadata_overrides: dict[str, str | int | float | bool] | None,
    prop_index: PropertyIndex | None,
    collection: chromadb.Collection,
    chroma_client: chromadb.PersistentClient | None,
    event_bus: EventBus | None,
    config: RagConfig,
    correlation_id: str,
    operation: str | None,
) -> EmbedPhaseResult:
    """Build chunk vectors, upsert to Chroma, write FTS entries.

    Assumes chunks is non-empty — callers must handle the empty-chunks path.
    """
    texts = [c.text for c in chunks]
    metadatas = [c.metadata for c in chunks]
    path_key = compute_path_key(source)
    ids: list[str] = []

    if metadata_overrides is not None:
        for metadata in metadatas:
            metadata.update(metadata_overrides)

    now = datetime.now(UTC).isoformat()
    for chunk_index, (metadata, chunk) in enumerate(
        zip(metadatas, chunks, strict=True)
    ):
        chunk_hash = compute_chunk_hash(chunk_index, chunk.text)
        metadata["chunk_hash"] = chunk_hash
        metadata["indexed_at"] = existing_timestamps.get(chunk_hash, now)
        ids.append(compose_chunk_id(path_key, chunk_hash))

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

    if event_bus is not None:
        for cid, meta in zip(ids, metadatas, strict=True):
            if chunk_metadata_is_noise(meta):
                await event_bus.publish_nowait(
                    rag_chunk_noise_tagged(
                        chunk_id=cid,
                        source=source,
                        noise_reason=meta.get("noise_reason", "unspecified_noise"),
                    )
                )

    embed_texts = texts
    cache_rows_to_store: list = []
    cache_hit_flags = [False] * len(chunks)
    if config.contextualize_model:
        embed_texts, cache_rows_to_store, cache_hit_flags = (
            await _run_contextualization_phase(
                source=source,
                source_hash=source_hash,
                chunks=chunks,
                metadatas=metadatas,
                texts=texts,
                prop_index=prop_index,
                context_model=config.contextualize_model,
                context_client_timeout_s=config.contextualize_client_timeout_s,
                correlation_id=correlation_id,
                operation=operation,
            )
        )

    diff = partition_embed_work(
        ids=ids,
        existing_ids=existing_ids,
        cache_hit_flags=cache_hit_flags,
    )
    processed_indices = diff.processed_indices
    processed_ids = subset_by_indices(ids, processed_indices)
    processed_embed_texts = subset_by_indices(embed_texts, processed_indices)
    processed_texts = subset_by_indices(texts, processed_indices)
    processed_metadatas = subset_by_indices(metadatas, processed_indices)

    if event_bus is not None:
        await event_bus.publish_nowait(
            rag_embed_diff_evaluated(
                file=source,
                operation_id=correlation_id,
                total_chunks=len(ids),
                processed_chunks=diff.processed_count,
                skipped_chunks=diff.skipped_count,
                legacy_id_count=diff.legacy_id_count,
                operation=operation,
            )
        )

    if processed_embed_texts:
        if event_bus is not None:
            await event_bus.publish_nowait(
                rag_embed_started(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(processed_embed_texts),
                    operation=operation,
                )
            )
        embeddings = await embed_chunks(processed_embed_texts)
        if event_bus is not None:
            await event_bus.publish_nowait(
                rag_embed_completed(
                    file=source,
                    operation_id=correlation_id,
                    chunk_count=len(processed_embed_texts),
                    operation=operation,
                )
            )

        await _upsert_chroma_chunk_batches(
            chroma_client=chroma_client,
            collection=collection,
            event_bus=event_bus,
            source=source,
            correlation_id=correlation_id,
            operation=operation,
            ids=processed_ids,
            embeddings=embeddings,
            texts=processed_texts,
            metadatas=processed_metadatas,
        )

        if prop_index is not None:
            if event_bus is not None:
                await event_bus.publish_nowait(
                    rag_property_write_started(
                        file=source,
                        operation_id=correlation_id,
                        chunk_count=len(processed_ids),
                        property_entries=0,
                        operation=operation,
                    )
                )
            await prop_index.fts.insert_batch(
                [
                    (cid, source, text)
                    for cid, text in zip(processed_ids, processed_texts, strict=True)
                ]
            )
            if event_bus is not None:
                await event_bus.publish_nowait(
                    rag_property_write_completed(
                        file=source,
                        operation_id=correlation_id,
                        chunk_count=len(processed_ids),
                        property_entries=0,
                        operation=operation,
                    )
                )

    stale_ids = compute_stale_ids(existing_ids, ids)
    return EmbedPhaseResult(
        chunks=chunks,
        ids=ids,
        texts=texts,
        metadatas=metadatas,
        cache_rows_to_store=cache_rows_to_store,
        stale_ids=stale_ids,
    )
