"""Generic UDS/TCP client factories for internal service HTTP calls."""

from __future__ import annotations

import httpx
import os

RAG_SOCKET_PATH = os.environ.get("RAG_SOCKET_PATH", "/tmp/universal-protocol/rag.sock")
DEFAULT_RAG_URL = f"unix://{RAG_SOCKET_PATH}"

CORTEX_SOCKET_PATH = os.environ.get(
    "CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock"
)
DEFAULT_CORTEX_URL = f"unix://{CORTEX_SOCKET_PATH}"


def parse_rag_url(url: str) -> tuple[str | None, str]:
    """Parse a service URL into (uds_path, base_url)."""
    url = url.strip()
    if url.startswith("unix://"):
        rest = url[7:]
        path = rest if rest else RAG_SOCKET_PATH
        if not path.startswith("/"):
            path = f"/{path}"
        return path, "http://localhost"
    return None, url.rstrip("/")


def make_sync_client(
    url: str = DEFAULT_RAG_URL,
    *,
    timeout: float = 10.0,
) -> httpx.Client:
    """Create a sync httpx client for UDS or TCP service URLs."""
    uds_path, base_url = parse_rag_url(url)
    if uds_path:
        transport = httpx.HTTPTransport(uds=uds_path)
        return httpx.Client(transport=transport, base_url=base_url, timeout=timeout)
    return httpx.Client(base_url=base_url, timeout=timeout)


def make_async_client(
    url: str = DEFAULT_RAG_URL,
    *,
    timeout: float = 10.0,
) -> httpx.AsyncClient:
    """Create an async httpx client for UDS or TCP service URLs."""
    uds_path, base_url = parse_rag_url(url)
    if uds_path:
        transport = httpx.AsyncHTTPTransport(uds=uds_path)
        return httpx.AsyncClient(
            transport=transport, base_url=base_url, timeout=timeout
        )
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)
