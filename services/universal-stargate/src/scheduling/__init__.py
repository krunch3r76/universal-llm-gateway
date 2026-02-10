# ruff: noqa: N999
"""
Scheduling system for Universal Stargate.

Architecture: WebSocket-first control plane with event-driven consumers.

Event-Driven Architecture (Phase 2 Complete):
- No polling loops - all state changes emitted from WebSocket callbacks
- GATEWAY_STATE_CHANGED events emitted directly from GatewayWebSocketClient
  on connect/disconnect (not from polling loop)
- Cache refresh via reconnection callbacks in GatewayInitializer

Consumers react to events:
- RoutingConsumer: Gateway availability for routing decisions
- MetricsConsumer: Performance and reliability statistics
- MonitoringConsumer: Uptime/downtime tracking, WebSocket push
"""

from .consumers import (
    MetricsConsumer,
    ModelCacheConsumer,
    ModelExecutionTracker,
    ModelLoadingConsumer,
    MonitoringConsumer,
    ResourceUpdateConsumer,
    RoutingConsumer,
    RoutingDecisionConsumer,
    RoutingMetricsConsumer,
)
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
    # Gateway state types (used by consumers for event payload comparison)
    "GatewayState",
    "ConnectivityState",
    "HealthState",
    # Event utilities
    "StateTransitionDebugger",
    "EventRateLimiter",
    "format_state_transition_for_logging",
    "validate_state_change_payload",
    # Logging and errors
    "GatewayLogger",
    "GatewayError",
    "ConnectivityError",
    "HealthError",
    "GatewayTimeoutError",
    "ModelLoadError",
    "ModelUnloadError",
    "NoHealthyGatewaysError",
    # Event consumers
    "ModelExecutionTracker",
    "MetricsConsumer",
    "ModelCacheConsumer",
    "ModelLoadingConsumer",
    "MonitoringConsumer",
    "ResourceUpdateConsumer",
    "RoutingConsumer",
    "RoutingDecisionConsumer",
    "RoutingMetricsConsumer",
    # Routing metric events
    "REQUEST_ROUTED",
    "MODEL_LOAD_INITIATED",
    "MODEL_LOAD_COMPLETED",
    "TOKEN_COUNT_COMPLETED",
]
