"""Post-commit success path for a completed index funnel run."""

from __future__ import annotations

import time

from universal_logging import get_logger

from services.rag.chunk_filters import chunk_metadata_is_noise
from services.rag.events.indexing import (
    rag_file_indexed,
    rag_file_indexing_failure_cleared,
)
from services.rag.models import IndexResult

from .. import state
from .delete import _enqueue_for_extraction

logger = get_logger(__name__)


async def _finalize_index_success(
    *,
    source: str,
    chunks: list,
    stale_ids: list[str],
    metadatas: list[dict],
    start: float,
    correlation_id: str,
    operation: str | None,
) -> IndexResult:
    """Clear failure row, enqueue extraction, emit rag.file.indexed, return result."""
    if state._property_index is not None:
        cleared = await state._property_index.clear_indexing_failure(source)
        if cleared and state._event_bus is not None:
            await state._event_bus.publish_nowait(
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
        await state._event_bus.publish_nowait(
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
