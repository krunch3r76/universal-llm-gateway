"""Stargate scheduling package — event bus wiring, consumers, and gateway state.

Architecture: WebSocket-first control plane with event-driven consumers.
This package re-exports gateway state types, event utilities, error classes,
and consumer implementations used by higher-level scheduling/routing systems.

Event-Driven Architecture (Phase 2 Complete):
- No polling loops - all state changes emitted from WebSocket callbacks
- GATEWAY_STATE_CHANGED events emitted directly from GatewayWebSocketClient
  on connect/disconnect (not from polling loop)
- Cache refresh via reconnection callbacks in GatewayInitializer

Event consumers:
- MetricsConsumer: Performance and reliability statistics
- ModelCacheConsumer: Cache synchronization for model availability snapshots
- ModelLoadingConsumer: Model load lifecycle updates
- MonitoringConsumer: Uptime/downtime tracking and status push
- ResourceUpdateConsumer: Resource and capacity state updates
- RoutingConsumer: Gateway availability for routing decisions
- RoutingDecisionConsumer: Routing decision stream processing
- RoutingMetricsConsumer: Routing performance metrics aggregation

Event utilities:
- StateTransitionDebugger: Structured state transition debugging support
- EventRateLimiter: Per-signal/event throttling
- format_state_transition_for_logging: Canonical transition log formatting
- validate_state_change_payload: Payload validation helpers

Gateway state and errors:
- ConnectivityState, HealthState, GatewayState
- ConnectivityError, HealthError, GatewayError, GatewayTimeoutError,
  ModelLoadError, ModelUnloadError, NoHealthyGatewaysError
"""

# ruff: noqa: N999, F401
# pyright: reportUnusedImport=false, reportUnsupportedDunderAll=false

from .consumers.metrics_consumer import MetricsConsumer
from .consumers.model_cache_consumer import ModelCacheConsumer
from .consumers.model_loading_consumer import ModelLoadingConsumer
from .consumers.monitoring_consumer import MonitoringConsumer
from .consumers.resource_consumer import ResourceUpdateConsumer
from .consumers.routing_consumer import RoutingConsumer
from .consumers.routing_decision_consumer import RoutingDecisionConsumer
from .consumers.routing_metrics_consumer import RoutingMetricsConsumer
from .event_utils import (
    EventRateLimiter,
    StateTransitionDebugger,
    format_state_transition_for_logging,
    validate_state_change_payload,
)
from .events import (
    MODEL_LOAD_COMPLETED,
    MODEL_LOAD_INITIATED,
    REQUEST_ROUTED,
    TOKEN_COUNT_COMPLETED,
)
from .gateway_errors import (
    ConnectivityError,
    GatewayError,
    GatewayTimeoutError,
    HealthError,
    ModelLoadError,
    ModelUnloadError,
    NoHealthyGatewaysError,
)
from .gateway_logging import GatewayLogger
from .gateway_state import ConnectivityState, GatewayState, HealthState

__all__ = [
    name
    for name in globals()
    if not name.startswith("_") and name not in {"annotations"}
]
