"""Pure indexing failure classification (no rag_service package import)."""

from __future__ import annotations

import asyncio
import zipfile

import httpx


def classify_http_status_error(exc: httpx.HTTPStatusError) -> tuple[str, str]:
    """Classify an HTTP status error for indexing failure persistence."""
    code = exc.response.status_code
    if code in (503, 504):
        return ("transient", f"http_{code}")
    if exc.response.is_server_error:
        return ("transient", "http_5xx")
    if code == 429:
        return ("transient", "http_429")
    return ("permanent", "http_client_error")


def _precomputed_failure(
    exc: BaseException,
) -> tuple[str, str] | None:
    category = getattr(exc, "failure_category", None)
    reason = getattr(exc, "failure_reason", None)
    if isinstance(category, str) and isinstance(reason, str) and category and reason:
        return (category, reason)
    return None


def _classify_from_cause_chain(exc: BaseException) -> tuple[str, str] | None:
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, httpx.HTTPStatusError):
            return classify_http_status_error(cause)
        cause = cause.__cause__
    return None


def classify_indexing_failure(
    exc: BaseException,
    *,
    chunk_count: int,
) -> tuple[str, str]:
    """Classify an indexing exception as permanent vs transient."""
    del chunk_count  # reserved for future chunk-aware rules

    precomputed = _precomputed_failure(exc)
    if precomputed is not None:
        return precomputed

    from_cause = _classify_from_cause_chain(exc)
    if from_cause is not None:
        return from_cause

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
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_http_status_error(exc)

    return ("transient", "unclassified")


# Private aliases for callers that imported underscore-prefixed names.
_classify_http_status_error = classify_http_status_error
_classify_indexing_failure = classify_indexing_failure
