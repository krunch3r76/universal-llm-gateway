"""File deletion and extraction-queue helpers for the RAG indexing pipeline.

``_delete_file`` is the watcher's delete callback (re-exported from the
``indexing`` package) for a removed file: it serializes on the per-source FIFO
gate from ``source_path_gate``, deletes the source's Chroma chunks, removes
source-scoped property-index metadata (keeping the article row), and emits
``rag_file_deleted`` when chunks existed. ``_enqueue_for_extraction`` is shared
with ``finalize`` and ``file_guards`` and queues a source for async knowledge
extraction only when its scope allows extraction.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

from services.rag.events.indexing import rag_file_deleted
from services.rag.models import DeleteResult

from .. import state

logger = get_logger(__name__)


async def _enqueue_for_extraction(source: str) -> None:
    """Queue a source for async extraction if the scope allows it."""
    if state._config is None or state._property_index is None:
        return
    scope = state._config.get_scope_for_path(source)
    if state._config.knowledge_extraction.should_extract_scope(scope):
        await state._property_index.enqueue_extraction(source)


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
    if existing_ids and state._event_bus is not None:
        await state._event_bus.publish_nowait(
            rag_file_deleted(file=source, deleted=deleted)
        )
    return DeleteResult(file=source, deleted=deleted)


async def _delete_file(file_path: Path) -> DeleteResult:
    """Delete all indexed chunks for a removed file under per-source gate."""
    from ..source_path_gate import acquire_source_path, release_source_path

    source = str(file_path.resolve())
    await acquire_source_path(source)
    try:
        return await _delete_file_impl(source)
    finally:
        await release_source_path(source)
