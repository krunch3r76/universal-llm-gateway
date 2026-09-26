"""Proxy client error types and HTTP transport error normalization for Stargate calls.

Defines ``ProxyClientError`` (re-exported from ``proxy_client``) plus private helpers
that build status-based messages and map httpx transport failures to it: ConnectError ->
503, RemoteProtocolError -> 502. Imported by the chat completion, streaming, vector
(embeddings / rerank) and timeout-diagnostics modules so pipeline handlers catch one
error type.
"""

from __future__ import annotations

from typing import Any, NoReturn

import httpx


class ProxyClientError(Exception):
    """Single error type raised by ProxyClient operations against the Stargate proxy.

    Carries an optional HTTP ``status_code`` (503 for connect failures, 502 for protocol
    errors, upstream status otherwise) and raw ``detail`` payload. Caught by pipeline
    handlers such as ``call_model`` and ``model_fallback`` to trigger fallback to other
    ranked model candidates or to surface step failures.
    """

    def __init__(
        self, message: str, status_code: int | None = None, detail: Any = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _error_message(status_code: int, detail: Any) -> str:
    """Build error message that includes upstream error detail when available."""
    base = f"Stargate returned {status_code}"
    if isinstance(detail, dict):
        error_info = detail.get("error", {})
        if isinstance(error_info, dict) and "message" in error_info:
            return f"{base}: {error_info['message']}"
    return base


def _raise_httpx_transport_error(exception: httpx.HTTPError) -> NoReturn:
    """Normalize httpx transport errors to ProxyClientError.

    Converts ConnectError -> 503, RemoteProtocolError -> 502, other HTTPError
    to generic ProxyClientError. Used by chat, embeddings, rerank paths.
    """
    if isinstance(exception, httpx.ConnectError):
        raise ProxyClientError(
            f"Failed to connect to Stargate: {exception}",
            status_code=503,
            detail=str(exception),
        ) from exception
    if isinstance(exception, httpx.RemoteProtocolError):
        raise ProxyClientError(
            (
                "HTTP protocol error (connection closed or invalid response): "
                f"{exception}"
            ),
            status_code=502,
            detail=str(exception),
        ) from exception

    error_msg = str(exception) if str(exception) else exception.__class__.__name__
    raise ProxyClientError(
        f"HTTP error: {error_msg}",
        detail=str(exception),
    ) from exception
