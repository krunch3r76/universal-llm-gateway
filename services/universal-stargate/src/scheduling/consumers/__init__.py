"""Event-bus consumers for Stargate scheduling metrics, routing, and resources.

Re-exports MetricsConsumer, routing/model consumers, and resource update handlers
used by the scheduling package public surface.
"""

# ruff: noqa: F401
# pyright: reportUnusedImport=false, reportUnsupportedDunderAll=false

from .metrics_consumer import MetricsConsumer
from .model_cache_consumer import ModelCacheConsumer
from .model_loading_consumer import ModelLoadingConsumer
from .monitoring_consumer import MonitoringConsumer
from .resource_consumer import ResourceUpdateConsumer
from .routing_consumer import RoutingConsumer
from .routing_decision_consumer import RoutingDecisionConsumer
from .routing_metrics_consumer import RoutingMetricsConsumer

__all__ = [
    name
    for name in globals()
    if not name.startswith("_") and name not in {"annotations"}
]
