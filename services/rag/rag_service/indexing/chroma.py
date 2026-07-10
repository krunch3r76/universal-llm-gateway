"""ChromaDB upsert helpers for the indexing pipeline."""

from __future__ import annotations

from typing import Any

from chromadb.utils.batch_utils import create_batches

from services.rag.events.indexing import (
    rag_chroma_upsert_completed,
    rag_chroma_upsert_started,
)


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
