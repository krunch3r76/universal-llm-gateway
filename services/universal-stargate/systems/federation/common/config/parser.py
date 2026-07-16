"""
Federation configuration parsing orchestrator.

Delegates to specific parsers for each config section.
"""

from typing import Any

from .env_expansion import expand_env_vars
from .remote_parser import _parse_remotes
from .schema import (
    AllowedPeerConfig,
    ConfigurationError,
    ConnectionLimits,
    FederationConfig,
    HTTPPoolConfig,
    LocalEdgeConfig,
    MasterConnectionConfig,
    OrchestrationConfigSchema,
    OverflowPolicy,
    StargateMode,
    TelemetryBackpressure,
    TLSConfig,
    WSServerConfig,
)


def _parse_federation_config(config_dict: dict[str, Any]) -> FederationConfig:
    """
    Parse federation config section from YAML (orchestrator).

    Delegates to specific parsers for each config section.

    Args:
        config_dict: Federation section from YAML

    Returns:
        Fully parsed FederationConfig
    """
    # Fail-fast: Reject old local_gateway config
    if config_dict.get("local_gateway"):
        raise ConfigurationError(
            "local_gateway is no longer supported. "
            "Use local_edge with federation protocol instead. "
            "See docs/gpu-relay-deployment.md for migration."
        )

    # Phase 4: Reject removed worker_limits (capacity is gateway-published)
    if "worker_limits" in config_dict:
        raise ConfigurationError(
            "REMOVED: federation.worker_limits. "
            "Capacity is now managed by Gateway's FifoCapacityGate (parallel_slots)."
        )
    for idx, remote in enumerate(config_dict.get("remotes", [])):
        if isinstance(remote, dict) and "worker_limits" in remote:
            raise ConfigurationError(
                f"REMOVED: federation.remotes[{idx}].worker_limits. "
                "Capacity: Gateway FifoCapacityGate (parallel_slots)."
            )

    mode = StargateMode(config_dict.get("mode", "edge"))

    # Expand environment variables in stargate_id (may contain ${REMOTE_ID})
    stargate_id_raw = config_dict.get("stargate_id", "")
    stargate_id = expand_env_vars(stargate_id_raw) if stargate_id_raw else ""

    node_id_raw = config_dict.get("node_id", "")
    node_id = expand_env_vars(node_id_raw) if node_id_raw else ""

    remotes = _parse_remotes(config_dict.get("remotes", []))

    return FederationConfig(
        mode=mode,
        stargate_id=stargate_id,
        node_id=node_id,
        protocol_version=config_dict.get("protocol_version", "1.0"),
        max_hops=config_dict.get("max_hops", 3),
        telemetry_stale_threshold_ms=config_dict.get(
            "telemetry_stale_threshold_ms", 5000
        ),
        telemetry_unreachable_threshold_ms=config_dict.get(
            "telemetry_unreachable_threshold_ms", 10000
        ),
        ping_interval_ms=config_dict.get("ping_interval_ms", 15000),
        reconnect_interval_ms=config_dict.get("reconnect_interval_ms", 1000),
        max_reconnect_delay_ms=config_dict.get(
            "max_reconnect_delay_ms", 30000
        ),  # 30s cap default
        max_reconnect_attempts=config_dict.get("max_reconnect_attempts", 10),
        health_check_interval_ms=config_dict.get("health_check_interval_ms", 5000),
        require_tls=config_dict.get("require_tls", True),
        tls=_parse_tls(config_dict.get("tls")),
        connection_limits=_parse_connection_limits(
            config_dict.get("connection_limits")
        ),
        remotes=remotes,
        local_edge=_parse_local_edge(config_dict.get("local_edge")),
        allowed_peers=_parse_allowed_peers(config_dict.get("allowed_peers")),
        http_pool=_parse_http_pool(config_dict.get("http_pool")),
        telemetry_backpressure=_parse_telemetry_backpressure(
            config_dict.get("telemetry_backpressure")
        ),
        master=_parse_master_connection(config_dict.get("master")),
        ws_server=_parse_ws_server(config_dict.get("ws_server")),
        orchestration=_parse_orchestration(config_dict.get("orchestration")),
        disable_websocket=config_dict.get("disable_websocket", False),
        snapshot_interval_ms=config_dict.get("snapshot_interval_ms", 120_000),
    )


def _parse_local_edge(
    edge_data: dict[str, Any] | None,
) -> LocalEdgeConfig | None:
    """
    Parse local_edge config (Remote mode).

    Args:
        edge_data: Edge config dict from YAML or None

    Returns:
        LocalEdgeConfig if data provided, None otherwise

    Raises:
        ConfigurationError: If required fields missing or env vars not set
    """
    if not edge_data:
        return None

    # Validate required fields
    if not edge_data.get("socket_path"):
        raise ConfigurationError("local_edge missing required 'socket_path'")
    if not edge_data.get("stargate_id"):
        raise ConfigurationError("local_edge missing required 'stargate_id'")
    if not edge_data.get("api_key"):
        raise ConfigurationError("local_edge missing required 'api_key'")

    try:
        api_key = expand_env_vars(edge_data["api_key"]).strip()
    except ValueError as e:
        raise ConfigurationError(f"Invalid local_edge config: {e}") from e

    return LocalEdgeConfig(
        socket_path=edge_data["socket_path"],
        stargate_id=edge_data["stargate_id"],
        api_key=api_key,
    )


def _parse_allowed_peers(
    peers_data: list[dict[str, Any]] | None,
) -> list[AllowedPeerConfig]:
    """
    Parse allowed_peers config (Edge mode).

    Args:
        peers_data: List of peer config dicts from YAML

    Returns:
        List of AllowedPeerConfig objects

    Raises:
        ConfigurationError: If required fields missing or env vars not set
    """
    if not peers_data:
        return []

    result = []
    for peer in peers_data:
        if not peer.get("stargate_id"):
            raise ConfigurationError("allowed_peers entry missing 'stargate_id'")
        if not peer.get("api_key"):
            raise ConfigurationError("allowed_peers entry missing 'api_key'")

        try:
            api_key = expand_env_vars(peer["api_key"]).strip()
        except ValueError as e:
            raise ConfigurationError(
                f"Invalid allowed_peers config for {peer.get('stargate_id')}: {e}"
            ) from e

        result.append(
            AllowedPeerConfig(
                stargate_id=peer["stargate_id"],
                api_key=api_key,
            )
        )

    return result


def _parse_http_pool(pool_data: dict[str, Any] | None) -> HTTPPoolConfig:
    """
    Parse http_pool config with defaults (Master mode).

    Args:
        pool_data: HTTP pool config dict from YAML or None

    Returns:
        HTTPPoolConfig with values from YAML or defaults
    """
    if not pool_data:
        return HTTPPoolConfig()

    return HTTPPoolConfig(
        max_connections=pool_data.get("max_connections", 100),
        max_keepalive_connections=pool_data.get("max_keepalive_connections", 20),
        max_connections_per_remote=pool_data.get("max_connections_per_remote", 20),
    )


def _parse_telemetry_backpressure(
    bp_data: dict[str, Any] | None,
) -> TelemetryBackpressure:
    """
    Parse telemetry_backpressure config with defaults.

    Args:
        bp_data: Telemetry backpressure config dict from YAML or None

    Returns:
        TelemetryBackpressure with values from YAML or defaults
    """
    if not bp_data:
        return TelemetryBackpressure()

    overflow_policy = OverflowPolicy(bp_data.get("overflow_policy", "drop_oldest"))
    return TelemetryBackpressure(
        max_queue_per_remote=bp_data.get("max_queue_per_remote", 100),
        max_events_per_second=bp_data.get("max_events_per_second", 1000.0),
        overflow_policy=overflow_policy,
    )


def _parse_tls(tls_data: dict[str, Any] | None) -> TLSConfig | None:
    """
    Parse TLS config.

    Args:
        tls_data: TLS config dict from YAML or None

    Returns:
        TLSConfig if data provided, None otherwise
    """
    if not tls_data:
        return None

    return TLSConfig(
        cert_file=tls_data["cert_file"],
        key_file=tls_data["key_file"],
        ca_file=tls_data["ca_file"],
    )


def _parse_connection_limits(limits_data: dict[str, Any] | None) -> ConnectionLimits:
    """
    Parse connection_limits config with defaults.

    Args:
        limits_data: Connection limits config dict from YAML or None

    Returns:
        ConnectionLimits with values from YAML or defaults
    """
    if not limits_data:
        return ConnectionLimits()

    return ConnectionLimits(
        max_unauthenticated_per_ip=limits_data.get("max_unauthenticated_per_ip", 5),
        max_federation_per_peer=limits_data.get("max_federation_per_peer", 10),
        auth_deadline_seconds=limits_data.get("auth_deadline_seconds", 5),
    )


def _parse_master_connection(
    master_data: dict[str, Any] | None,
) -> MasterConnectionConfig | None:
    """
    Parse master connection config with env expansion (Remote mode - NEW).

    Remote Stargates use this to configure outbound connection TO Master.

    Args:
        master_data: Master connection config dict from YAML or None

    Returns:
        MasterConnectionConfig if data provided, None otherwise

    Raises:
        ConfigurationError: If required fields missing or env vars not set
    """
    if not master_data:
        return None

    # Validate required fields
    if not master_data.get("stargate_id"):
        raise ConfigurationError("Master config missing required 'stargate_id'")
    if not master_data.get("url"):
        raise ConfigurationError("Master config missing required 'url'")
    if not master_data.get("api_key"):
        raise ConfigurationError("Master config missing required 'api_key'")

    try:
        api_key = expand_env_vars(master_data["api_key"]).strip()
        url = expand_env_vars(master_data["url"]).strip()
    except ValueError as e:
        raise ConfigurationError(f"Invalid master config: {e}") from e

    return MasterConnectionConfig(
        stargate_id=master_data["stargate_id"],
        url=url,
        api_key=api_key,
    )


def _parse_ws_server(server_data: dict[str, Any] | None) -> WSServerConfig:
    """
    Parse WebSocket server config with defaults (Master mode - NEW).

    Args:
        server_data: WebSocket server config dict from YAML or None

    Returns:
        WSServerConfig with values from YAML or defaults
    """
    if not server_data:
        return WSServerConfig()

    return WSServerConfig(
        path=server_data.get("path", "/ws/federation/master"),
        max_connections=server_data.get("max_connections", 100),
        auth_deadline_seconds=server_data.get("auth_deadline_seconds", 5),
    )


def _parse_orchestration(orch_data: dict[str, Any] | None) -> OrchestrationConfigSchema:
    """
    Parse orchestration configuration.

    Schema defaults apply for any missing keys.

    Args:
        orch_data: Orchestration config dict from YAML or None

    Returns:
        OrchestrationConfigSchema with values from YAML or defaults
    """
    if not orch_data:
        return OrchestrationConfigSchema()

    return OrchestrationConfigSchema(
        load_timeout=orch_data.get("load_timeout", 300),
        coalesce_wait_timeout=orch_data.get("coalesce_wait_timeout", 330),
        telemetry_staleness_threshold=orch_data.get(
            "telemetry_staleness_threshold", 10.0
        ),
        load_retry_count=orch_data.get("load_retry_count", 2),
        load_retry_delay=orch_data.get("load_retry_delay", 1.0),
        load_retry_backoff=orch_data.get("load_retry_backoff", 1.5),
        load_retry_max_delay=orch_data.get("load_retry_max_delay", 10.0),
        load_retry_jitter=orch_data.get("load_retry_jitter", 0.1),
    )
