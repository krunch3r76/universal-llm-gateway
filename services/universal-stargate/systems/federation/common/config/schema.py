"""
Federation configuration schema (dataclasses only).

All configuration dataclasses for federation system.
"""

import os
import socket
from dataclasses import dataclass, field
from enum import StrEnum


class ConfigurationError(Exception):
    """Federation configuration error."""

    pass


class EndpointCategory(StrEnum):
    """
    Endpoint categories for worker limit grouping.

    Each category has independent worker pools to allow
    concurrent execution of different workload types.
    """

    GENERATION = "generation"  # /v1/chat/completions, /v1/completions
    EMBEDDING = "embedding"  # /v1/embeddings
    RERANK = "rerank"  # /api/v1/rerank → /v1/rerank


class StargateMode(StrEnum):
    """
    Stargate federation operating modes.

    INVARIANT: ∀ s ∈ S: |{MASTER, REMOTE, EDGE} ∩ {mode(s)}| = 1

    Mode semantics:
        MASTER: Pure orchestrator, no local execution, routes to Relays/Edges
        REMOTE: Relay Stargate - may have local_edge (Edge container) or
                gateway (direct execution)
        EDGE: Passive endpoint, accepts inbound only, proxies local Gateway

    Terminology:
        - REMOTE mode = "Relay" (process-level role)
        - local_edge = Edge container with Gateway (optional for Relay)
        - Edge = Container that proxies Gateway
    """

    MASTER = "master"
    REMOTE = "remote"  # Relay Stargate
    EDGE = "edge"


@dataclass(slots=True, kw_only=True)
class TLSConfig:
    """TLS configuration for federation connections."""

    cert_file: str
    key_file: str
    ca_file: str


@dataclass(slots=True, kw_only=True)
class ConnectionLimits:
    """Connection limits for federation peers."""

    max_unauthenticated_per_ip: int = 5
    max_federation_per_peer: int = 10
    auth_deadline_seconds: int = 5


@dataclass(slots=True, kw_only=True)
class HTTPPoolConfig:
    """HTTP connection pool configuration."""

    max_connections: int = 100
    max_keepalive_connections: int = 20
    max_connections_per_remote: int = 20


class OverflowPolicy(StrEnum):
    """
    Telemetry queue overflow policies.

    Note: This enum is defined here (federation config) AND in the library
    module (universal_event_bus.backpressure). They are intentionally separate
    to avoid circular imports. Both use identical values and can be compared
    via string equality since StrEnum inherits from str.
    """

    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"
    BACKPRESSURE = "backpressure"


@dataclass(slots=True, kw_only=True)
class TelemetryBackpressure:
    """Telemetry rate limiting configuration."""

    max_queue_per_remote: int = 100
    max_events_per_second: float = 1000.0  # High default (safety valve)
    overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST


class TelemetryLogLevel(StrEnum):
    """
    Telemetry logging levels for Remote nodes.

    Used in FederationConfig schema validation.
    """

    DEBUG = "DEBUG"  # All snapshots + deltas
    INFO = "INFO"  # Only state changes (deltas)
    ERROR = "ERROR"  # Only errors


class TelemetryWSInitiator(StrEnum):
    """
    Which side initiates the telemetry WebSocket connection.

    Values:
    - remote: Remote/Relay connects to Master (current default topology)
    - master: Master connects to Edge (/ws/federation/edge) (Golem port-tunnel topology)
    """

    REMOTE = "remote"
    MASTER = "master"


@dataclass(slots=True, kw_only=True)
class RemoteStargateConfig:
    """
    Configuration for a remote Stargate (used in Master mode).

    Master uses this to decide how to receive telemetry from each remote:
    - disable_websocket=False (default): WebSocket telemetry (direction selected by
      telemetry_ws_initiator)
    - disable_websocket=True: Master polls via HTTP GET /api/v1/federation/telemetry
    """

    stargate_id: str
    url: str
    api_key: str  # Expanded from ${ENV_VAR}

    # Telemetry transport selection (per-remote)
    disable_websocket: bool = False  # True = HTTP polling, False = WebSocket (default)

    # Telemetry WebSocket initiation direction (only applies if disable_websocket=False)
    telemetry_ws_initiator: TelemetryWSInitiator = TelemetryWSInitiator.REMOTE

    # HTTP polling interval (only used if disable_websocket=True)
    telemetry_poll_interval_ms: int = 5000  # 5 seconds default

    # Telemetry logging configuration
    telemetry_log_level: TelemetryLogLevel = TelemetryLogLevel.INFO
    """
    Telemetry logging verbosity (DEBUG/INFO/ERROR).

    Default: INFO (lean logging for Golem)
    """

    @property
    def telemetry_transport(self) -> str:
        """
        Telemetry transport mechanism (Layer C detail).

        Returns:
            "ws" for WebSocket (default), "http_polling" for HTTP polling
        """
        return "http_polling" if self.disable_websocket else "ws"

    @property
    def role_description(self) -> str:
        """
        Human-readable role description for logging.

        Returns:
            Description string like "relay (telemetry=ws)"
        """
        return f"relay (telemetry={self.telemetry_transport})"


@dataclass(slots=True, kw_only=True)
class LocalEdgeConfig:
    """
    Local Edge Stargate configuration for Unix socket federation.

    Used when a Relay connects to a network-isolated Edge Stargate
    over Unix socket using federation protocol.

    INVARIANT: socket_path ⟹ federation protocol (not Gateway protocol)
    """

    socket_path: str
    stargate_id: str
    api_key: str  # Expanded from ${ENV_VAR} - required for federation auth


@dataclass(slots=True, kw_only=True)
class AllowedPeerConfig:
    """
    Allowed peer configuration for Edge mode inbound connections.

    Edge Stargates authenticate inbound federation connections
    using this allowlist (same pattern as Master's remotes list).
    """

    stargate_id: str
    api_key: str  # Expanded from ${ENV_VAR}


@dataclass(slots=True, kw_only=True)
class MasterConnectionConfig:
    """
    Configuration for connecting TO a Master (used in Remote mode).

    Remote Stargates use this to initiate outbound connections to Master.

    Phase 2 Contract: RemoteWebSocketClient reads:
        - stargate_id: For peer_id property
        - url: For WebSocket connection (converted to wss://)
        - api_key: For federation_auth message
    """

    stargate_id: str
    url: str  # Master's base URL (http:// or https://)
    api_key: str  # Expanded from ${ENV_VAR}


@dataclass(slots=True, kw_only=True)
class WSServerConfig:
    """
    WebSocket server configuration (used in Master mode).

    Master Stargates use this to configure the server that accepts Remote connections.

    Phase 3 Contract: MasterAuthHandler reads:
        - auth_deadline_seconds: For asyncio.wait_for timeout
        - max_connections: For connection limit check
    """

    path: str = "/ws/federation/master"
    max_connections: int = 100
    auth_deadline_seconds: int = 5


@dataclass(slots=True, kw_only=True)
class CircuitBreakerConfig:
    """Circuit breaker configuration for federation health."""

    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_max_requests: int = 3


@dataclass(slots=True, kw_only=True)
class OrchestrationConfigSchema:
    """
    Orchestration configuration embedded in FederationConfig.

    This is the SCHEMA version (mutable dataclass for config loading).
    See orchestration/config.py for the frozen runtime version.

    CONSTRAINT: coalesce_wait_timeout >= load_timeout + 30
    (enforced at runtime in OrchestrationConfig.__post_init__)
    """

    load_timeout: int = 300
    coalesce_wait_timeout: int = 330
    telemetry_staleness_threshold: float = 10.0
    load_retry_count: int = 2
    load_retry_delay: float = 1.0
    load_retry_backoff: float = 1.5
    load_retry_max_delay: float = 10.0
    load_retry_jitter: float = 0.1  # Prevents thundering herd


@dataclass(slots=True, kw_only=True)
class FederationConfig:
    """
    Complete federation configuration.

    Mode determines which fields are required:
    - EDGE: allowed_peers optional (for accepting Relay connections)
    - MASTER: remotes, ws_server, http_pool, telemetry_backpressure required
    - REMOTE: master required, local_edge optional (for connecting to Edge)

    INVARIANT: execution_capable = false ⟹ mode = MASTER ∧ ¬∃ gateway_connection
    (gateway_connection = main config gateway.url ∨ gateway.socket_path)
    """

    mode: StargateMode = StargateMode.EDGE
    stargate_id: str = ""  # Defaults to hostname if empty
    node_id: str = ""  # Canonical node identity for affinity matching

    # Execution capability (derived at config load time from main gateway section)
    # Set by config loader: false only for router-only Master
    # (no gateway.url/socket_path)
    execution_capable: bool = True

    # Protocol
    protocol_version: str = "1.0"
    max_hops: int = 3

    # Timeouts (milliseconds)
    telemetry_stale_threshold_ms: int = 5000
    telemetry_unreachable_threshold_ms: int = 10000
    ping_interval_ms: int = 15000  # VPS-safe default (must be ≤30000)

    # Reconnection - CRITICAL: 30s max for bounded backoff
    reconnect_interval_ms: int = 1000
    max_reconnect_delay_ms: int = 30000  # CHANGED from 60000 - bounded backoff
    max_reconnect_attempts: int = 10
    health_check_interval_ms: int = 5000

    # TLS
    require_tls: bool = True
    tls: TLSConfig | None = None

    # Limits
    connection_limits: ConnectionLimits = field(default_factory=ConnectionLimits)

    # Master mode fields
    remotes: list[RemoteStargateConfig] = field(default_factory=list)
    http_pool: HTTPPoolConfig = field(default_factory=HTTPPoolConfig)
    telemetry_backpressure: TelemetryBackpressure = field(
        default_factory=TelemetryBackpressure
    )
    ws_server: WSServerConfig = field(default_factory=WSServerConfig)

    # Remote mode fields
    local_edge: LocalEdgeConfig | None = None
    master: MasterConnectionConfig | None = None

    # Edge mode fields
    allowed_peers: list[AllowedPeerConfig] = field(default_factory=list)
    # When False, Edge accepts any peer without credential checks
    # (trusted topology, e.g. network_mode=none + Unix socket)
    federation_auth_enabled: bool = True

    # Remote mode: disable outbound WebSocket client (for Golem/restrictive networks)
    # When True, Remote will NOT attempt to connect to Master via WebSocket
    # Master must poll this Remote via HTTP to receive telemetry
    disable_websocket: bool = False

    # Edge mode: periodic full-state snapshot interval (milliseconds)
    # Reconciles Master's view of loaded_models after disconnect/reconnect.
    # 0 = disabled (current behavior — snapshot only at wiring time).
    snapshot_interval_ms: int = 120_000

    # Circuit breaker (Master mode) - explicit field with defaults
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)

    # Orchestration (Master mode) - NEW
    orchestration: OrchestrationConfigSchema = field(
        default_factory=OrchestrationConfigSchema
    )

    # Telemetry configuration (Remote mode)
    telemetry_log_level: TelemetryLogLevel = TelemetryLogLevel.INFO
    """
    Default telemetry log level for Remote nodes (DEBUG/INFO/ERROR).

    Can be overridden per-remote in RemoteStargateConfig.telemetry_log_level.
    """

    # HTTP polling configuration (Golem-optimized, Master mode)
    fast_poll_interval_ms: int = 5000
    """
    Fast polling interval when remote has active requests.

    Default: 5000 (5s) - responsive during inference.
    """

    fast_poll_cooldown_ms: int = 30000
    """
    Continue fast polling for this duration after last request completes.

    Catches post-inference cleanup (model unload, VRAM release).

    Default: 30000 (30s).
    """

    def __post_init__(self) -> None:
        """Set defaults and validate after initialization."""
        if not self.stargate_id:
            self.stargate_id = socket.gethostname()
        if not self.node_id:
            self.node_id = os.environ.get("NODE_ID", "")
        if not self.node_id:
            self.node_id = (
                self.stargate_id.removeprefix("edge-")
                .removeprefix("relay-")
                .removeprefix("master-")
                .removesuffix("-gateway")
            )
