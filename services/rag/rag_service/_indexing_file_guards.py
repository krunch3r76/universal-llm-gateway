"""Pre-chunking and post-chunking guard helpers for _index_file_impl."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from services.rag.events.articles import rag_article_path_moved
from services.rag.events.indexing import rag_file_deleted, rag_file_skipped
from services.rag.indexing_helpers import check_pdf_duplicate, migrate_chroma_source
from services.rag.models import IndexResult

from . import state

logger = logging.getLogger(__name__)


async def _handle_pdf_duplicate_or_move(
    *,
    file_path: Path,
    source: str,
    source_hash: str,
    collection: object,
    article_sync_old_path: str | None,
    force: bool,
    correlation_id: str,
    operation: str | None,
) -> IndexResult | None:
    """Reconcile Chroma for moved article rows and check for PDF duplicate/move.

    Returns an IndexResult (skip result) when the file is a true duplicate,
    or None when the caller should continue with normal indexing.
    """
    if article_sync_old_path is not None:
        n_migrated = migrate_chroma_source(
            collection, source_hash, article_sync_old_path, source
        )
        if n_migrated > 0:
            logger.info(
                "Chroma source migrated: %s → %s (%d chunks)",
                article_sync_old_path,
                source,
                n_migrated,
            )

    if file_path.suffix.lower() != ".pdf" or force:
        return None

    dup_result = check_pdf_duplicate(collection, source_hash, source)
    if dup_result is None:
        return None

    old_path = dup_result.duplicate_of
    if old_path is not None and not Path(old_path).exists():
        # Moved PDF: old path is gone — this is a rename, not a true duplicate.
        # Migrate Chroma chunk metadata and continue; the normal "unchanged"
        # path below will update indexed_sources and create the article row.
        n_migrated = migrate_chroma_source(collection, source_hash, old_path, source)
        logger.info(
            "PDF source migrated in Chroma: %s → %s (%d chunks)",
            old_path,
            source,
            n_migrated,
        )
        if n_migrated > 0 and state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_article_path_moved(
                    old_path=old_path,
                    new_path=source,
                    content_hash=source_hash,
                )
            )
        # Fall through (return None) — Chroma now has source=new_path.
        return None

    # True duplicate: old path still exists on disk.
    if old_path is not None:
        logger.info(
            "PDF duplicate detected: %s is duplicate of %s", source, old_path
        )
    if state._event_bus is not None:
        await state._event_bus.publish_nowait(
            rag_file_skipped(
                file=source,
                reason="duplicate_pdf",
                operation_id=correlation_id,
                operation=operation,
            )
        )
    return dup_result


async def _handle_empty_chunks(
    *,
    source: str,
    existing_ids: list[str],
    prop_index: object,
    collection: object,
    source_stat: object,
    source_hash: str,
    schema_version: int,
    extraction_model: str | None,
    correlation_id: str,
    operation: str | None,
) -> IndexResult:
    """Handle the case where chunking produces no output.

    Deletes any existing chunks and returns a zero-indexed result.
    """
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
        await state._event_bus.publish_nowait(
            rag_file_deleted(
                file=source,
                deleted=len(existing_ids),
                operation_id=correlation_id,
                operation=operation,
            )
        )
    return IndexResult(deleted=len(existing_ids), indexed=0, unchanged=False, file=source)
