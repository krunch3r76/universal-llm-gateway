"""Failure classification and persistence helpers for the indexing pipeline."""

from __future__ import annotations

import asyncio
import logging
import zipfile

from services.rag.events.indexing import (
    rag_file_indexing_failure_recorded,
    rag_indexing_failure_persist_failed,
)

from . import state

logger = logging.getLogger(__name__)


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
    if isinstance(exc, zipfile.BadZipFile) or exc_type_name == "PackageNotFoundError":
        return ("permanent", "corrupt_archive")
    if "exceeds max batch size" in msg_lower:
        return ("permanent", "exceeds_chroma_max_batch_size")

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
