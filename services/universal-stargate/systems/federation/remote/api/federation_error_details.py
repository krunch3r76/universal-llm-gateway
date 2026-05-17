"""
Federation error detail extraction.

Preserves structured upstream HTTP error payloads (canonical envelopes, FastAPI
detail dicts) across relay and direct forwarding boundaries so that error codes,
retryability, and other metadata survive without lossy stringification.
"""

import httpx


def extract_structured_detail(response: httpx.Response) -> dict | str:
    """Extract structured error detail from an HTTP error response.

    Preserves canonical error envelopes end-to-end so upstream routing/capacity
    logic can inspect code, retryable, etc. without heuristic re-mapping.

    Handles streaming responses where body may not have been read yet
    (raises httpx.ResponseNotRead).

    Returns:
        Parsed dict if response contains valid JSON dict, else raw text
    """
    try:
        payload = response.json()
        if isinstance(payload, dict):
            # Canonical envelope or FastAPI {"detail": {...}} — preserve as dict
            if "detail" in payload and isinstance(payload["detail"], dict):
                return payload["detail"]
            if "code" in payload or "message" in payload:
                return payload
            return payload
    except Exception:
        pass
    try:
        return response.text[:1000]
    except Exception:
        # Streaming response with unread body (httpx.ResponseNotRead)
        return f"HTTP {response.status_code} (body not available)"


__all__ = ["extract_structured_detail"]
