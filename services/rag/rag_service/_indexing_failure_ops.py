"""Failure classification and persistence helpers for the indexing pipeline."""

from __future__ import annotations

import logging

from services.rag.indexing_failure_classifier import (
    classify_http_status_error as _classify_http_status_error,
)
from services.rag.indexing_failure_classifier import (
    classify_indexing_failure as _classify_indexing_failure,
)
from services.rag.events.indexing import (
    rag_file_indexing_failure_recorded,
    rag_indexing_failure_persist_failed,
)

from . import state

logger = logging.getLogger(__name__)


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
        error_type = type(exc).__qualname__
        error_message = str(exc) or error_type
        attempt_count = await state._property_index.record_indexing_failure(
            source=source,
            failure_category=category,
            failure_reason=reason,
            error_message=error_message,
            error_type=error_type,
            source_hash=source_hash,
            source_size_bytes=source_size_bytes,
            source_mtime_ns=source_mtime_ns,
        )
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_file_indexing_failure_recorded(
                    file=source,
                    failure_category=category,
                    failure_reason=reason,
                    attempt_count=attempt_count,
                    error_type=error_type,
                    error_head=error_message[:200],
                )
            )
    except Exception as record_exc:
        if state._event_bus is not None:
            await state._event_bus.publish_nowait(
                rag_indexing_failure_persist_failed(
                    file=source,
                    error=f"{type(record_exc).__qualname__}: {record_exc}",
                )
            )
