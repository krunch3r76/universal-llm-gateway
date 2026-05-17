"""Stargate proxy client package.

Public surface:
- ProxyClient: main async HTTP client for pipeline-to-Stargate calls
- ProxyClientConfig: config container (stargate_url + request_timeout)
- ProxyClientError: typed exception for all proxy failures

All other modules are private implementation details (mixins, helpers).
Existing import paths such as `from ..execution.proxy_client import ProxyClient`
continue to work via this __init__.py (package shadow pattern).
"""

from .client import ProxyClient
from .configuration import ProxyClientConfig
from .errors import ProxyClientError

__all__ = ["ProxyClient", "ProxyClientConfig", "ProxyClientError"]
