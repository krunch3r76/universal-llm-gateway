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


def classify_indexing_failure(
    exc: BaseException,
    *,
    chunk_count: int,
) -> tuple[str, str]:
    """Classify an indexing exception as permanent vs transient."""
    del chunk_count  # reserved for future chunk-aware rules
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
