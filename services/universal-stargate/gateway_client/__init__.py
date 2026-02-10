"""Gateway client package - hybrid WebSocket/HTTP client."""

from .client import GatewayClient
from .config import GatewayConfig, ModelMetadata

__all__ = [
    "GatewayClient",
    "GatewayConfig",
    "ModelMetadata",
]
