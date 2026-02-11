"""Upstream error extraction, detection, and classification.

Helpers for interpreting HTTP error responses from federated gateways
so that the proxy can assign the correct ErrorCode and retryability.
"""

from typing import Any

from universal_protocol import ErrorCode

_MAX_UPSTREAM_ERROR_CHARS: int = 20_000


def _truncate_text(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"... [truncated {len(value) - max_chars} chars]"


def extract_upstream_error_payload(response: Any) -> dict[str, Any]:
    """Best-effort extraction of upstream error payload.

    Invariants:
    - Never raises (diagnostics must not mask the original error).
    - Always returns JSON-serializable data.

    Inner catches are intentionally silent: each accessor (.json(), .text)
    may fail independently and we want to capture whichever parts succeed.
    The outer catch returns a sentinel so callers always get a dict.
    """
    try:
        status_code: int | None = getattr(response, "status_code", None)
        headers_obj = getattr(response, "headers", None)
        headers: dict[str, str] = dict(headers_obj) if headers_obj else {}

        try:
            body_json: Any | None = response.json()
        except Exception:  # noqa: BLE001 — silent by design (partial extraction)
            body_json = None

        try:
            body_text: str | None = response.text
        except Exception:  # noqa: BLE001 — silent by design (partial extraction)
            body_text = None

        if isinstance(body_text, str):
            body_text = _truncate_text(body_text, max_chars=_MAX_UPSTREAM_ERROR_CHARS)

        return {
            "status_code": status_code,
            "headers": headers,
            "body_json": body_json,
            "body_text": body_text,
        }
    except Exception as e:  # noqa: BLE001
        return {"extraction_error": repr(e)}


def is_upstream_model_not_loaded(upstream_payload: dict[str, Any]) -> bool:
    """Detect upstream 'model_not_loaded' error code.

    This is semantically a transient resource state (often eviction/load TOCTOU),
    even when surfaced as HTTP 400 by the upstream gateway.

    Checks structured JSON first; falls back to substring match on body_text
    (best-effort heuristic for when JSON parsing failed upstream).
    """
    body_json = upstream_payload.get("body_json")
    if isinstance(body_json, dict):
        error_obj = body_json.get("error")
        if isinstance(error_obj, dict):
            return error_obj.get("code") == "model_not_loaded"

    body_text = upstream_payload.get("body_text")
    return isinstance(body_text, str) and "model_not_loaded" in body_text


def map_upstream_status_to_error_code(
    upstream_status_code: int,
    upstream_payload: dict[str, Any],
) -> tuple[ErrorCode, bool]:
    """Map upstream HTTP status to (ErrorCode, retryable).

    Note: This maps *upstream* semantics; our proxy may still return 502.
    """
    if upstream_status_code == 400:
        if is_upstream_model_not_loaded(upstream_payload):
            return ErrorCode.RESOURCE_UNAVAILABLE, True
        return ErrorCode.INVALID_REQUEST, False
    if upstream_status_code in {401, 403, 404}:
        return ErrorCode.INVALID_REQUEST, False
    if upstream_status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return ErrorCode.RESOURCE_UNAVAILABLE, True
    if 400 <= upstream_status_code < 500:
        return ErrorCode.INVALID_REQUEST, False
    return ErrorCode.UNEXPECTED_ERROR, True
