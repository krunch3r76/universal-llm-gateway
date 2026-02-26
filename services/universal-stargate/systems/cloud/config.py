"""
Cloud proxy client configuration.

Reads the ``cloud_proxy`` section from stargate.yaml. The only required
field is the proxy URL (loopback). All provider-specific config (API keys,
prefixes, concurrency) lives in the proxy's own config file.

INVARIANT: ∀ cloud_proxy.url: scheme ∈ {http, unix} ∧ host ∈ {localhost, 127.0.0.1}
           Stargate must never make outbound requests to non-local addresses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from universal_logging import get_logger

logger = get_logger(__name__)

_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


@dataclass(slots=True, kw_only=True)
class CloudProxyConfig:
    """Configuration for connecting to the cloud proxy service."""

    url: str


def _is_local_url(url: str) -> bool:
    """Return True if *url* targets loopback or a Unix socket.

    Enforces structural privacy: Stargate never opens outbound connections.
    """
    if url.startswith("unix://"):
        return True
    parsed = urlparse(url)
    return parsed.hostname in _ALLOWED_HOSTS


def parse_cloud_proxy_config(raw: dict[str, Any] | None) -> CloudProxyConfig | None:
    """Parse the cloud_proxy section from stargate.yaml.

    Returns None if the section is absent, has no valid URL, or the URL
    points to a non-local host (which would violate structural privacy).
    """
    if not raw or not isinstance(raw, dict):
        return None

    url = raw.get("url", "")
    if not isinstance(url, str) or not url.strip():
        logger.warning("cloud_proxy.url is missing or empty — skipping cloud proxy")
        return None

    url = url.strip().rstrip("/")

    if not _is_local_url(url):
        logger.error(
            "cloud_proxy.url must target localhost/loopback or a Unix socket, "
            "got %r — skipping cloud proxy (structural privacy violation)",
            url,
        )
        return None

    return CloudProxyConfig(url=url)
