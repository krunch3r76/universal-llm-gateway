"""
Federation configuration loading and parsing.

Handles YAML loading and config parsing.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from universal_logging import get_logger

from .env_expansion import expand_env_vars
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

_VALID_MODES = frozenset(m.value for m in StargateMode)

logger = get_logger(__name__)


def load_federation_config(config_path: Path | None = None) -> FederationConfig:
    """
    Load federation configuration from stargate_config.yaml.

    If config_path not provided:
    1. Check STARGATE_CONFIG env var
    2. Fall back to default location (config/stargate_config.yaml)

    Environment overrides:
        STARGATE_MODE: Override federation mode (master|remote|edge).
            Allows same config file to serve multiple roles.

    Args:
        config_path: Optional path to config file

    Returns:
        FederationConfig with loaded settings (defaults to EDGE if no config)

    Raises:
        ConfigurationError: If STARGATE_MODE is set to an invalid value

    Note:
        Synchronous I/O is acceptable here - startup only, not request path
    """
    if config_path is None:
        env_config = os.getenv("STARGATE_CONFIG")
        if env_config:
            config_path = Path(env_config).resolve()
            logger.debug(
                f"Loading federation config from STARGATE_CONFIG: {config_path}"
            )
        else:
            config_path = Path("config/stargate_config.yaml").resolve()
            logger.debug(f"Loading federation config from default: {config_path}")
    else:
        # Resolve provided path to absolute
        config_path = config_path.resolve()

    if not config_path.exists():
        logger.info(f"No config file found at {config_path}, using edge mode")
        return FederationConfig()

    with open(config_path) as f:
        data = yaml.safe_load(f)

    federation_section = data.get("federation", {})
    if not federation_section:
        return FederationConfig()

    from .parser import _parse_federation_config
    from .validation import _set_execution_capable

    fed_config = _parse_federation_config(federation_section)

    # STARGATE_MODE env var overrides config file mode
    # Allows same config to serve multiple roles (e.g. remote config reused as master)
    mode_override = os.getenv("STARGATE_MODE")
    if mode_override:
        mode_lower = mode_override.strip().lower()
        if mode_lower not in _VALID_MODES:
            raise ConfigurationError(
                f"Invalid STARGATE_MODE='{mode_override}'. "
                f"Must be one of: {', '.join(sorted(_VALID_MODES))}"
            )
        fed_config.mode = StargateMode(mode_lower)
        logger.info(
            f"Federation mode overridden by STARGATE_MODE: {fed_config.mode.value}"
        )

    # Set execution_capable based on gateway presence
    gateway_cfg = data.get("gateway", {})
    _set_execution_capable(fed_config, gateway_cfg)

    # Propagate authorization.enabled → federation_auth_enabled
    auth_section = data.get("authorization", {})
    fed_config.federation_auth_enabled = auth_section.get("enabled", True)

    return fed_config


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
        load_timeout=orch_data.get("load_timeout", 180),
        coalesce_wait_timeout=orch_data.get("coalesce_wait_timeout", 210),
        telemetry_staleness_threshold=orch_data.get(
            "telemetry_staleness_threshold", 10.0
        ),
        load_retry_count=orch_data.get("load_retry_count", 2),
        load_retry_delay=orch_data.get("load_retry_delay", 1.0),
        load_retry_backoff=orch_data.get("load_retry_backoff", 1.5),
        load_retry_max_delay=orch_data.get("load_retry_max_delay", 10.0),
        load_retry_jitter=orch_data.get("load_retry_jitter", 0.1),
    )


def log_startup_banner(config: FederationConfig) -> None:
    """
    Log startup banner with federation mode.

    Args:
        config: Federation configuration to display
    """
    mode_emoji = {
        StargateMode.MASTER: "👑",
        StargateMode.REMOTE: "🛰️",
        StargateMode.EDGE: "🏠",
    }

    emoji = mode_emoji.get(config.mode, "❓")
    logger.info(f"{emoji} Stargate Federation: {config.mode.value.upper()}")
    logger.info(f"   stargate_id: {config.stargate_id}")
    logger.info(f"   protocol: {config.protocol_version}")

    if config.mode == StargateMode.MASTER:
        relay_info = [
            f"{r.stargate_id} (telemetry={r.telemetry_transport})"
            for r in config.remotes
        ]
        logger.info(f"   relay stargates: {relay_info}")
    elif config.mode == StargateMode.REMOTE:
        if config.master:
            logger.info(f"   master: {config.master.stargate_id}")
        if config.local_edge:
            logger.info(
                f"   local edge: {config.local_edge.stargate_id} "
                f"(socket: {config.local_edge.socket_path})"
            )
    elif config.mode == StargateMode.EDGE:
        auth_label = "enabled" if config.federation_auth_enabled else "disabled"
        logger.info(f"   federation auth: {auth_label}")
        if config.allowed_peers:
            peer_ids = [p.stargate_id for p in config.allowed_peers]
            logger.info(f"   allowed peers: {peer_ids}")
