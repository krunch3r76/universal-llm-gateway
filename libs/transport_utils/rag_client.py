"""Service client factory — UDS/TCP transport resolution and httpx client creation.

Generic factory for any service reachable via UDS (``unix:///path``) or TCP
(``http://host:port``). Originally built for RAG; used by all UDS-connected
services including cortex-api. RAG-specific helpers (``resolve_rag_base_url``)
remain here alongside generic ones (``make_sync_client``, ``make_async_client``).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml

RAG_SOCKET_PATH = "/tmp/universal-protocol/rag.sock"
DEFAULT_RAG_URL = f"unix://{RAG_SOCKET_PATH}"

CORTEX_SOCKET_PATH = "/tmp/universal-protocol/cortex-api.sock"
DEFAULT_CORTEX_URL = f"unix://{CORTEX_SOCKET_PATH}"
_STARGATE_CONFIG_PATH = Path.home() / ".gateway" / "stargate.yaml"


def resolve_rag_base_url() -> str:
    """Resolve RAG base URL from stargate.yaml (UDS default, TCP opt-in).

    Returns unix:///path or http://host:port. Used by pipeline handlers
    for runtime transport resolution.
    """
    if not _STARGATE_CONFIG_PATH.exists():
        return DEFAULT_RAG_URL
    try:
        data = yaml.safe_load(_STARGATE_CONFIG_PATH.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return DEFAULT_RAG_URL
    rag = data.get("rag")
    if not isinstance(rag, dict):
        return DEFAULT_RAG_URL
    host = rag.get("host")
    port_val = rag.get("port")
    if host is None or port_val is None:
        return DEFAULT_RAG_URL
    if not str(host).strip():
        return DEFAULT_RAG_URL
    try:
        port = int(port_val)
    except (TypeError, ValueError):
        return DEFAULT_RAG_URL
    if not (1 <= port <= 65535):
        return DEFAULT_RAG_URL
    return f"http://{host}:{port}"


def parse_rag_url(url: str) -> tuple[str | None, str]:
    """Parse RAG URL into (uds_path, base_url).

    Returns:
        (uds_path, base_url): For unix:///path, uds_path is the path and base_url
        is "http://localhost". For http://host:port, uds_path is None and
        base_url is the full URL.
    """
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
    """Create a sync httpx.Client for RAG requests.

    Uses UDS transport when url starts with unix://, otherwise TCP.
    Clients must be closed (use as context manager).
    """
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
    """Create an async httpx.AsyncClient for RAG requests.

    Uses UDS transport when url starts with unix://, otherwise TCP.
    Clients must be closed (use as context manager).
    """
    uds_path, base_url = parse_rag_url(url)
    if uds_path:
        transport = httpx.AsyncHTTPTransport(uds=uds_path)
        return httpx.AsyncClient(
            transport=transport, base_url=base_url, timeout=timeout
        )
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)
