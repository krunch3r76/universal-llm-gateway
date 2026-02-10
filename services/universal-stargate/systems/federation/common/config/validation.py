"""
Federation configuration validation.
"""

from typing import Any

from .schema import FederationConfig, StargateMode


def _set_execution_capable(
    fed_config: FederationConfig, gateway_cfg: dict[str, Any]
) -> None:
    """
    Set execution_capable based on mode and gateway configuration.

    INVARIANT:
      mode = MASTER ⟹ execution_capable = (|remotes| > 0 ∨ local_edge configured)
      mode = REMOTE ⟹ execution_capable = (local_edge configured)
      mode = EDGE ⟹ execution_capable = (gateway.url or gateway.socket_path)

    Treats empty strings as "no connection" (consistent with validation).

    Args:
        fed_config: Federation config to modify
        gateway_cfg: Gateway section from stargate_config.yaml
    """
    if fed_config.mode == StargateMode.MASTER:
        # Master is execution-capable if it has remotes OR local_edge
        fed_config.execution_capable = (
            len(fed_config.remotes) > 0 or fed_config.local_edge is not None
        )
    elif fed_config.mode == StargateMode.REMOTE:
        # Remote is execution-capable if local_edge configured
        # (Relay aggregates Edge telemetry, forwards to Master)
        fed_config.execution_capable = fed_config.local_edge is not None
    else:  # EDGE
        # Standalone uses gateway section
        url = gateway_cfg.get("url")
        socket_path = gateway_cfg.get("socket_path")
        has_url = url and isinstance(url, str) and url.strip()
        has_socket = (
            socket_path and isinstance(socket_path, str) and socket_path.strip()
        )
        fed_config.execution_capable = bool(has_url or has_socket)
