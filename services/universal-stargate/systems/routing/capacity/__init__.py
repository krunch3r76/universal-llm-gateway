"""
Per-model admission control for Master Stargate.

Tracks concurrency capacity per (gateway_id, model_id) and provides
event-driven slot reservation via self-releasing CapacityToken.
"""

from .pool import CapacityPool, CapacityToken

__all__ = ["CapacityPool", "CapacityToken"]
