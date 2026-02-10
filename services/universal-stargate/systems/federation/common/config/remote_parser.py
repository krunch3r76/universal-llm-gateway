"""
Remote Stargate configuration parsing.
"""

from typing import Any

from .env_expansion import expand_env_vars
from .schema import ConfigurationError, RemoteStargateConfig, TelemetryWSInitiator


def _parse_remotes(remotes_data: list[dict[str, Any]]) -> list[RemoteStargateConfig]:
    """
    Parse remotes list with validation and env expansion (Master mode).

    Args:
        remotes_data: List of remote config dicts from YAML

    Returns:
        List of validated RemoteStargateConfig objects

    Raises:
        ConfigurationError: If required fields missing or env vars not set
    """
    return [_parse_remote_config(remote_data) for remote_data in remotes_data]


def _require_remote_fields(remote_data: dict[str, Any]) -> tuple[str, str, str]:
    """
    Validate and extract required remote config fields.

    Args:
        remote_data: Remote config dict from YAML

    Returns:
        Tuple of (stargate_id, url, api_key_template)

    Raises:
        ConfigurationError: If required fields missing
    """
    if not remote_data.get("stargate_id"):
        raise ConfigurationError("Remote config missing required 'stargate_id'")
    if not remote_data.get("url"):
        raise ConfigurationError("Remote config missing required 'url'")
    if not remote_data.get("api_key"):
        raise ConfigurationError("Remote config missing required 'api_key'")

    return (
        remote_data["stargate_id"],
        remote_data["url"],
        remote_data["api_key"],
    )


def _expand_api_key(remote_id: str, api_key_template: str) -> str:
    """
    Expand environment variables in API key.

    Args:
        remote_id: Remote Stargate ID (for error messages)
        api_key_template: API key with potential ${VAR} patterns

    Returns:
        Expanded API key (stripped of whitespace)

    Raises:
        ConfigurationError: If env var expansion fails
    """
    try:
        return expand_env_vars(api_key_template).strip()
    except ValueError as e:
        raise ConfigurationError(f"Invalid remote config for {remote_id}: {e}") from e


def _parse_remote_optional_fields(
    remote_data: dict[str, Any],
) -> tuple[bool, int, TelemetryWSInitiator]:
    """
    Parse optional remote config fields with defaults.

    Args:
        remote_data: Remote config dict from YAML

    Returns:
        Tuple of (disable_websocket, telemetry_poll_interval_ms, telemetry_ws_initiator)
    """
    disable_websocket = remote_data.get("disable_websocket", False)
    telemetry_poll_interval_ms = remote_data.get("telemetry_poll_interval_ms", 5000)
    initiator_raw = remote_data.get("telemetry_ws_initiator", "remote")
    try:
        telemetry_ws_initiator = TelemetryWSInitiator(initiator_raw)
    except ValueError as e:
        raise ConfigurationError(
            f"Invalid telemetry_ws_initiator: {initiator_raw!r} "
            f"(expected one of {[v.value for v in TelemetryWSInitiator]})"
        ) from e
    return disable_websocket, telemetry_poll_interval_ms, telemetry_ws_initiator


def _parse_remote_config(remote_data: dict[str, Any]) -> RemoteStargateConfig:
    """
    Parse a single remote configuration.

    Orchestrates validation, expansion, and dataclass construction.

    Args:
        remote_data: Remote config dict from YAML

    Returns:
        Validated RemoteStargateConfig

    Raises:
        ConfigurationError: If required fields missing or env vars not set
    """
    stargate_id, url, api_key_template = _require_remote_fields(remote_data)
    api_key = _expand_api_key(stargate_id, api_key_template)
    disable_websocket, telemetry_poll_interval_ms, telemetry_ws_initiator = (
        _parse_remote_optional_fields(remote_data)
    )

    return RemoteStargateConfig(
        stargate_id=stargate_id,
        url=url,
        api_key=api_key,
        disable_websocket=disable_websocket,
        telemetry_ws_initiator=telemetry_ws_initiator,
        telemetry_poll_interval_ms=telemetry_poll_interval_ms,
    )
