"""
Stargate Federation System.

Enables distributed inference routing across network-isolated gateways.
"""

# Config
from universal_protocol.messages import TelemetrySource

from .common.config import (
    AllowedPeerConfig,
    ConfigurationError,
    ConnectionLimits,
    FederationConfig,
    HTTPPoolConfig,
    LocalEdgeConfig,
    MasterConnectionConfig,
    OverflowPolicy,
    RemoteStargateConfig,
    StargateMode,
    TelemetryBackpressure,
    TLSConfig,
    WSServerConfig,
    expand_env_vars,
    load_federation_config,
    log_startup_banner,
)

# ConnectionManager abstraction
from .common.connection_manager import ConnectionManager as FederationConnectionManager

# Health
from .common.health.status import (
    FederationHealth,
    FederationHealthHandler,
    RemoteHealthMetrics,
    RequestTrackerProtocol,
    get_federation_health,
)

# Metrics
from .common.metrics.prometheus import (
    FEDERATION_METRIC_SPECS,
    REQUIRED_ALERTS,
    FederationMetrics,
    get_metrics,
)

# Middleware
from .common.middleware.auth import (
    FederationAuthMiddleware,
    verify_federation_key,
)
from .common.middleware.endpoint_guard import (
    REMOTE_MODE_ALLOWED_PREFIXES,
    RemoteModeEndpointGuard,
)
from .common.middleware.header_sanitization import HeaderSanitizationMiddleware
from .common.middleware.hop_counting import HopCountMiddleware

# PeerConnection protocol
from .common.peer_connection import PeerConnection

# Message protocol
from .common.protocol import (
    FederationMessageType,
    MessageEnvelope,
    create_federation_auth,
    create_federation_auth_result,
    create_federation_init,
    create_federation_ping,
    create_federation_pong,
    create_model_busy,
    create_model_idle,
    create_model_load_failed,
    create_model_loaded,
    create_model_loading_started,
    create_model_unloaded,
    create_resource_update,
    create_telemetry_heartbeat,
    is_telemetry_type,
    parse_federation_message,
)

# Types
from .common.types import (
    FEDERATION_HEADERS,
    HEADER_FEDERATION_HOP_COUNT,
    HEADER_FEDERATION_KEY,
    HEADER_FEDERATION_SOURCE,
    HEADER_REQUEST_ID,
    PROTOCOL_VERSION,
    WS_CLOSE_AUTH_DEADLINE,
    WS_CLOSE_AUTH_FAILED,
    WS_CLOSE_IDENTITY_COLLISION,
    WS_CLOSE_PROTOCOL_MISMATCH,
    FederatedGateway,
    FederationRequestMetadata,
    RequestState,
    TrackedRequest,
    extract_resource_state,
    parse_telemetry_payload,
    validate_version,
)

# Integration
from .integration import (
    FederationIntegration,
    get_federation_integration,
    init_federation,
    shutdown_federation,
)

# Manager (Master-only, but exposed for convenience)
from .master.manager.federated_gateway_manager import FederatedGatewayManager

# Routing (Master-only, but exposed for convenience)
from .master.routing.forward import FederatedRequestForwarder
from .master.routing.orchestrator import MasterRequestTracker

# Remote API (exposed for convenience, but Remote-only)
from .remote.api.cancel import create_cancel_router
from .remote.api.inference import create_inference_router
from .remote.api.request_store import ActiveRequest, ActiveRequestStore

__all__ = [
    # Config
    "ConfigurationError",
    "FederationConfig",
    "StargateMode",
    "RemoteStargateConfig",
    "LocalEdgeConfig",
    "AllowedPeerConfig",
    "MasterConnectionConfig",
    "WSServerConfig",
    "TLSConfig",
    "ConnectionLimits",
    "HTTPPoolConfig",
    "TelemetryBackpressure",
    "OverflowPolicy",
    "load_federation_config",
    "log_startup_banner",
    "expand_env_vars",
    # NEW Abstractions
    "FederationConnectionManager",
    "PeerConnection",
    # Message Protocol
    "FederationMessageType",
    "MessageEnvelope",
    "create_federation_init",
    "create_federation_auth",
    "create_federation_auth_result",
    "create_federation_ping",
    "create_federation_pong",
    "create_resource_update",
    "create_model_loaded",
    "create_model_unloaded",
    "create_model_busy",
    "create_model_idle",
    "create_model_loading_started",
    "create_model_load_failed",
    "create_telemetry_heartbeat",
    "is_telemetry_type",
    "parse_federation_message",
    # Types
    "TelemetrySource",
    "FederationRequestMetadata",
    "TrackedRequest",
    "RequestState",
    "FederatedGateway",
    "HEADER_FEDERATION_SOURCE",
    "HEADER_FEDERATION_KEY",
    "HEADER_FEDERATION_HOP_COUNT",
    "HEADER_REQUEST_ID",
    "FEDERATION_HEADERS",
    "PROTOCOL_VERSION",
    "WS_CLOSE_PROTOCOL_MISMATCH",
    "WS_CLOSE_AUTH_DEADLINE",
    "WS_CLOSE_IDENTITY_COLLISION",
    "WS_CLOSE_AUTH_FAILED",
    "validate_version",
    "parse_telemetry_payload",
    "extract_resource_state",
    # Middleware
    "RemoteModeEndpointGuard",
    "REMOTE_MODE_ALLOWED_PREFIXES",
    "FederationAuthMiddleware",
    "verify_federation_key",
    "HopCountMiddleware",
    "HeaderSanitizationMiddleware",
    # API
    "create_inference_router",
    "create_cancel_router",
    "ActiveRequestStore",
    "ActiveRequest",
    # Manager
    "FederatedGatewayManager",
    # Routing
    "FederatedRequestForwarder",
    "MasterRequestTracker",
    # Health
    "FederationHealthHandler",
    "FederationHealth",
    "RemoteHealthMetrics",
    "RequestTrackerProtocol",
    "get_federation_health",
    # Metrics
    "FederationMetrics",
    "get_metrics",
    "FEDERATION_METRIC_SPECS",
    "REQUIRED_ALERTS",
    # Integration
    "FederationIntegration",
    "get_federation_integration",
    "init_federation",
    "shutdown_federation",
]
