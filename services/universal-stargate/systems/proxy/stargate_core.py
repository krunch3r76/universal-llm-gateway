"""
Compatibility wrapper for the modular Stargate proxy implementation.

Existing imports (e.g., `from stargate_core import StargateProxy`) continue to work
while the actual implementation lives under `proxy.stargate`.
"""

from .stargate import GATEWAY_URL, PROXY_PORT, StargateProxy

__all__ = ["StargateProxy", "GATEWAY_URL", "PROXY_PORT"]
