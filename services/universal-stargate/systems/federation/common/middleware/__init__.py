"""Federation middleware."""

from .auth import (
    EdgeFederationAuthMiddleware,
    FederationAuthMiddleware,
    verify_federation_key,
)
from .endpoint_guard import (
    EDGE_MODE_ALLOWED_PREFIXES,
    REMOTE_MODE_ALLOWED_PREFIXES,
    EdgeModeEndpointGuard,
    RemoteModeEndpointGuard,
)
from .header_sanitization import HeaderSanitizationMiddleware
from .hop_counting import HopCountMiddleware

__all__ = [
    "EdgeModeEndpointGuard",
    "EDGE_MODE_ALLOWED_PREFIXES",
    "RemoteModeEndpointGuard",
    "REMOTE_MODE_ALLOWED_PREFIXES",
    "EdgeFederationAuthMiddleware",
    "FederationAuthMiddleware",
    "verify_federation_key",
    "HopCountMiddleware",
    "HeaderSanitizationMiddleware",
]
