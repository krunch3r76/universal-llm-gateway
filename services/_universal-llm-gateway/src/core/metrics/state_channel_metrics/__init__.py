"""Metrics collection for state channel monitoring — package-shadow of state_channel_metrics.py.

Re-exports StateChannelMetricsCollector, ChannelMetrics, and the module singleton
so existing imports from src.core.metrics.state_channel_metrics keep working.
"""

from .channel_metrics import ChannelMetrics
from .collector import StateChannelMetricsCollector, state_channel_metrics

__all__ = [
    "ChannelMetrics",
    "StateChannelMetricsCollector",
    "state_channel_metrics",
]
