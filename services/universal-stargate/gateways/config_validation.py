"""
Gateway configuration schema validation.

INVARIANT: Fail-fast on invalid or legacy config formats.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised for invalid configuration."""

    pass


REQUIRED_GATEWAY_KEYS = {
    "resource_management",  # Always required
}

VALID_FEDERATION_MODES = {"master", "remote", "edge"}


def _reject_legacy_gateway_list(config: dict[str, Any]) -> None:
    """
    Reject legacy multi-gateway list format.

    Args:
        config: Full stargate config dict

    Raises:
        ConfigurationError: If legacy 'gateways' key present
    """
    if "gateways" in config:
        raise ConfigurationError(
            "MIGRATION REQUIRED: 'gateways' list format is removed.\n"
            "Update to single 'gateway' object format.\n"
            "See: docs/refactoring/federation-only-architecture-v2.md"
        )


def _validate_gateway_section_shape(gateway: dict[str, Any]) -> None:
    """
    Validate gateway section has required keys.

    Args:
        gateway: Gateway config section

    Raises:
        ConfigurationError: If required keys missing
    """
    for key in REQUIRED_GATEWAY_KEYS:
        if key not in gateway:
            raise ConfigurationError(f"Missing required gateway.{key}")


def _has_valid_gateway_connection(gateway: dict[str, Any]) -> bool:
    """
    Check if gateway has a valid connection configured.

    Treats empty strings as invalid (no connection).

    Args:
        gateway: Gateway config section

    Returns:
        True if url or socket_path is truthy string
    """
    url_value = gateway.get("url")
    socket_path_value = gateway.get("socket_path")

    # Empty strings are treated as "no connection"
    has_url = url_value and isinstance(url_value, str) and url_value.strip()
    has_socket = (
        socket_path_value
        and isinstance(socket_path_value, str)
        and socket_path_value.strip()
    )

    return bool(has_url or has_socket)


def _validate_gateway_connection_policy(
    gateway: dict[str, Any], federation: dict[str, Any]
) -> None:
    """
    Enforce federation mode-specific gateway connection policies.

    INVARIANT (BREAKING CHANGE - sole maintainer):
      mode = REMOTE ⟹ federation.local_edge.socket_path required
      mode = EDGE ⟹ gateway.(url ∨ socket_path) required
      mode = MASTER ⟹ gateway.(url ∨ socket_path) optional

    Args:
        gateway: Gateway config section
        federation: Federation config section

    Raises:
        ConfigurationError: If connection policy violated
    """
    fed_mode = federation.get("mode", "edge")

    # Fail-fast on unknown mode
    if fed_mode not in VALID_FEDERATION_MODES:
        raise ConfigurationError(
            f"Invalid federation mode '{fed_mode}'. "
            f"Must be one of: {', '.join(sorted(VALID_FEDERATION_MODES))}"
        )

    has_gateway_connection = _has_valid_gateway_connection(gateway)

    if fed_mode == "master":
        # Master mode: local gateway optional (router-only allowed)
        if not has_gateway_connection:
            remotes = federation.get("remotes", [])
            if not remotes:
                raise ConfigurationError(
                    "Master mode with no local gateway requires at least one remote "
                    "CONFIGURED in federation.remotes[]."
                )
            return
    elif fed_mode == "remote":
        # Remote mode: ONLY federation.local_edge (socket_path required)
        local_edge = federation.get("local_edge", {})
        fed_socket_path = local_edge.get("socket_path")

        has_fed_socket = (
            fed_socket_path
            and isinstance(fed_socket_path, str)
            and fed_socket_path.strip()
        )

        if not has_fed_socket:
            raise ConfigurationError(
                "Remote mode requires federation.local_edge.socket_path.\n"
                "This is the Unix socket connection to the local Edge Stargate.\n\n"
                "Example (relay topology with Unix socket):\n"
                "  federation:\n"
                "    mode: remote\n"
                "    local_edge:\n"
                '      socket_path: "/tmp/universal-protocol/edge.sock"\n'
                '      stargate_id: "edge-localhost"\n'
                '      api_key: "${FEDERATION_KEY_EDGE}"\n\n'
                "Note: Remote connects to Edge Stargate via federation protocol.\n"
                "Top-level gateway.socket_path is NOT used for Remote mode."
            )

        # Warn if top-level gateway connection also set (migration leftover)
        if has_gateway_connection:
            logger.warning(
                "Remote mode: Ignoring top-level gateway connection (using "
                "federation.local_edge.socket_path instead). "
                "Remove gateway.socket_path/url from config to silence this warning."
            )
        return
    else:
        # Edge mode: require gateway connection (direct Gateway access)
        if not has_gateway_connection:
            raise ConfigurationError(
                f"Gateway config requires 'url' or 'socket_path' for mode '{fed_mode}'."
            )

    # For non-Remote modes: If both url and socket_path specified, that's an error
    url_value = gateway.get("url")
    socket_path_value = gateway.get("socket_path")
    has_url = url_value and isinstance(url_value, str) and url_value.strip()
    has_socket = (
        socket_path_value
        and isinstance(socket_path_value, str)
        and socket_path_value.strip()
    )

    if has_url and has_socket:
        raise ConfigurationError(
            "Gateway config must have exactly one of: 'url' or 'socket_path' (not both)"
        )


def validate_gateway_config(config: dict[str, Any]) -> None:
    """
    Validate stargate configuration for gateway section requirements.

    Orchestrates validation steps: legacy format check, shape validation,
    and federation mode-specific connection policies.

    Args:
        config: Full stargate_config.yaml content (must have 'gateway' key)

    Raises:
        ConfigurationError: If config is invalid or uses legacy format
    """
    _reject_legacy_gateway_list(config)

    federation = config.get("federation", {})
    federation_mode = federation.get("mode", "edge")

    gateway = config.get("gateway")

    # Master mode is a pure orchestrator - no gateway section required
    if federation_mode == "master":
        if not gateway:
            # Master without gateway is valid (router-only)
            return
        # Master with gateway: validate shape and connection

    # Remote mode uses federation.local_gateway - gateway section optional
    # (only non-connection settings like timeouts are used from gateway section)
    if federation_mode == "remote":
        _validate_gateway_connection_policy(gateway or {}, federation)
        # Skip shape validation - Remote doesn't require gateway.resource_management
        return

    # Edge mode requires gateway section with full config
    if not gateway:
        raise ConfigurationError("Missing required 'gateway' section")

    _validate_gateway_section_shape(gateway)
    _validate_gateway_connection_policy(gateway, federation)


def derive_token_endpoint(config: dict[str, Any]) -> str:
    """
    Build token counting endpoint URL from gateway connection settings.

    Args:
        config: Full stargate_config.yaml content with validated 'gateway' section

    Returns:
        Token counting endpoint URL (TCP or Unix socket format)

    Raises:
        ConfigurationError: If no gateway connection configured
    """
    gateway_cfg = config["gateway"]

    # Check for unix:// URL first (before socket_path or TCP URL)
    gateway_url = gateway_cfg.get("url")
    if gateway_url and gateway_url.startswith("unix://"):
        # Unix socket mode - extract socket path and use httpx transport format
        socket_path = gateway_url[7:]  # Remove "unix://" prefix
        # Convert container path to host path if needed
        if socket_path.startswith("/sockets/"):
            socket_name = socket_path.split("/")[-1]
            socket_path = f"/tmp/universal-sockets/{socket_name}"
        return f"http+unix://{socket_path}:/api/v1/tokens/count"
    elif "socket_path" in gateway_cfg:
        # Unix socket mode - use httpx transport format
        socket_path = gateway_cfg["socket_path"]
        return f"http+unix://{socket_path}:/api/v1/tokens/count"
    elif "url" in gateway_cfg and gateway_cfg["url"]:
        # TCP mode
        return f"{gateway_cfg['url']}/api/v1/tokens/count"
    else:
        # No gateway configured (Master mode with federation only)
        # Return empty string - token counting will use federation forwarder
        return ""


def get_remote_connection_config(federation: dict[str, Any]) -> str:
    """
    Get local Edge Stargate connection for Remote mode.

    INVARIANT: Remote mode ONLY uses federation.local_edge.socket_path.

    Connection target: Unix socket to Edge Stargate (federation protocol).

    Args:
        federation: Federation config section

    Returns:
        Socket path to local Edge Stargate

    Raises:
        ConfigurationError: If not Remote mode or socket_path not configured
    """
    fed_mode = federation.get("mode")
    if fed_mode != "remote":
        raise ConfigurationError(
            f"get_remote_connection_config() called for mode '{fed_mode}' "
            "(only valid for mode=remote)"
        )

    local_edge = federation.get("local_edge", {})
    socket_path = local_edge.get("socket_path")

    if socket_path and isinstance(socket_path, str) and socket_path.strip():
        return socket_path.strip()

    raise ConfigurationError(
        "Remote mode requires federation.local_edge.socket_path.\n"
        "Configure the Unix socket connection to your local Edge Stargate."
    )
