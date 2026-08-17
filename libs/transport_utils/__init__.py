"""Transport utilities — UDS/TCP service client factories."""

from __future__ import annotations

from transport_utils.client_factory import (
    AGENT_BUS_SOCK,
    CLOUD_PROXY_SOCKET_PATH,
    CORTEX_API_SOCK,
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CLOUD_PROXY_URL,
    DEFAULT_CORTEX_URL,
    DEFAULT_EMAIL_BRIDGE_URL,
    DEFAULT_RAG_URL,
    DEFAULT_SMS_BRIDGE_URL,
    DEFAULT_STARGATE_URL,
    EMAIL_BRIDGE_SOCK,
    EVENTS_QUERY_SOCK,
    EVENTS_SUBSCRIBE_PATH,
    MANAGE_SOCKET,
    RAG_SOCKET_PATH,
    SMS_BRIDGE_SOCK,
    STARGATE_UNIX_SOCKET,
    make_async_client,
    make_sync_client,
    parse_rag_url,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'mcp', 'rag', 'stargate')

def resolve_rag_base_url() -> str:
    """Resolve the RAG base URL without importing YAML helpers eagerly."""
    from transport_utils.rag_client import resolve_rag_base_url as _resolve_rag_base_url

    return _resolve_rag_base_url()

__all__ = [
    "AGENT_BUS_SOCK",
    "CLOUD_PROXY_SOCKET_PATH",
    "CORTEX_API_SOCK",
    "DEFAULT_AGENT_BUS_URL",
    "DEFAULT_CLOUD_PROXY_URL",
    "DEFAULT_CORTEX_URL",
    "DEFAULT_EMAIL_BRIDGE_URL",
    "DEFAULT_SMS_BRIDGE_URL",
    "DEFAULT_RAG_URL",
    "DEFAULT_STARGATE_URL",
    "EMAIL_BRIDGE_SOCK",
    "SMS_BRIDGE_SOCK",
    "EVENTS_QUERY_SOCK",
    "EVENTS_SUBSCRIBE_PATH",
    "MANAGE_SOCKET",
    "RAG_SOCKET_PATH",
    "STARGATE_UNIX_SOCKET",
    "make_async_client",
    "make_sync_client",
    "parse_rag_url",
    "resolve_rag_base_url",
]
