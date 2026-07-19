"""Generic UDS/TCP client factories for internal service HTTP calls."""

from __future__ import annotations

import os

import httpx

RAG_SOCKET_PATH = os.environ.get("RAG_SOCKET_PATH", "/tmp/universal-protocol/rag.sock")
DEFAULT_RAG_URL = f"unix://{RAG_SOCKET_PATH}"

CORTEX_API_SOCK = os.environ.get(
    "CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock"
)
DEFAULT_CORTEX_URL = f"unix://{CORTEX_API_SOCK}"

AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
DEFAULT_AGENT_BUS_URL = f"unix://{AGENT_BUS_SOCK}"

# Resolution order for Stargate URL:
#   1. STARGATE_UNIX_SOCKET — UDS mode (edge container deployment).
#   2. STARGATE_URL          — explicit HTTP override (containerized callers
#                              that must reach the host Stargate, e.g.
#                              mcp-server with STARGATE_URL=http://io:9999).
#   3. http://localhost:STARGATE_PORT — host-process callers (e.g.
#                              frontier_consult inside Stargate itself).
STARGATE_UNIX_SOCKET: str | None = os.environ.get("STARGATE_UNIX_SOCKET") or None
if STARGATE_UNIX_SOCKET:
    DEFAULT_STARGATE_URL = f"unix://{STARGATE_UNIX_SOCKET}"
elif os.environ.get("STARGATE_URL"):
    DEFAULT_STARGATE_URL = os.environ["STARGATE_URL"]
else:
    DEFAULT_STARGATE_URL = f"http://localhost:{os.environ.get('STARGATE_PORT', '9999')}"

EVENTS_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
EVENTS_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"  # host ignored with UDS

MANAGE_SOCKET = os.environ.get("MANAGE_SOCKET", "/tmp/universal-protocol/manage.sock")

CLOUD_PROXY_SOCKET_PATH = os.environ.get(
    "CLOUD_PROXY_SOCKET_PATH", "/tmp/universal-protocol/cloud-proxy.sock"
)
DEFAULT_CLOUD_PROXY_URL = f"unix://{CLOUD_PROXY_SOCKET_PATH}"

EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)
DEFAULT_EMAIL_BRIDGE_URL = f"unix://{EMAIL_BRIDGE_SOCK}"

SMS_BRIDGE_SOCK = os.environ.get(
    "SMS_BRIDGE_SOCK", "/tmp/universal-protocol/sms-bridge.sock"
)
DEFAULT_SMS_BRIDGE_URL = f"unix://{SMS_BRIDGE_SOCK}"


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
