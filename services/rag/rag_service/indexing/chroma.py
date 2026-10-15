"""ChromaDB upsert helpers for the RAG indexing pipeline's embed phase.

``_upsert_chroma_chunk_batches`` is called by ``indexing/embed.py`` to write
chunk ids, embeddings, documents and metadata to a Chroma collection. It splits
the rows with ``chromadb.utils.batch_utils.create_batches`` so each call stays
under the backend's max batch size, and brackets every batch with
``rag_chroma_upsert_started`` / ``rag_chroma_upsert_completed`` events carrying
batch index and total. Raises RuntimeError when no Chroma client is initialized.
"""

from __future__ import annotations

from typing import Any

from chromadb.utils.batch_utils import create_batches

from services.rag.chroma_writer import UPSERT_BATCH_CAP, run_chroma
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
    sub_batches: list[tuple[list[str], Any, list[dict[str, Any]], list[str]]] = []
    for b_ids, b_embeddings, b_metadatas, b_documents in batches:
        for start in range(0, len(b_ids), UPSERT_BATCH_CAP):
            end = start + UPSERT_BATCH_CAP
            sub_batches.append(
                (
                    b_ids[start:end],
                    b_embeddings[start:end],
                    b_metadatas[start:end],
                    b_documents[start:end],
                )
            )
    batch_total = len(sub_batches)

    def _upsert_slice(
        ids_: list[str],
        embeddings_: Any,
        documents_: list[str],
        metadatas_: list[dict[str, Any]],
    ) -> None:
        collection.upsert(
            ids=ids_,
            embeddings=embeddings_,
            documents=documents_,
            metadatas=metadatas_,
        )

    for batch_index, (b_ids, b_embeddings, b_metadatas, b_documents) in enumerate(
        sub_batches
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
        await run_chroma(
            _upsert_slice,
            b_ids,
            b_embeddings,
            b_documents,
            b_metadatas,
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
