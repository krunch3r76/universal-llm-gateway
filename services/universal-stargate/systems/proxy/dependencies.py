"""Shared dependencies for the proxy application"""

from typing import Any

from fastapi import Request
from universal_logging import get_logger

from gateway_client import GatewayConfig
from systems.federation.common.config import FederationConfig

from .stargate_core import StargateProxy

logger = get_logger(__name__)

# Global variables
_proxy_instance: StargateProxy | None = None
_federation_config: FederationConfig | None = None


def init_proxy(fed_config: FederationConfig) -> StargateProxy:
    """
    Initialize the global proxy instance with federation config.

    Called once during app lifespan startup - NOT a FastAPI dependency.
    This function is separate from get_proxy() to prevent fed_config from
    being exposed as a body parameter in FastAPI's dependency resolution.
    """
    global _proxy_instance, _federation_config
    import os

    if _proxy_instance is not None:
        logger.warning("Proxy already initialized, returning existing instance")
        return _proxy_instance

    config_path = os.environ.get("STARGATE_CONFIG", "config/stargate_config.yaml")
    _federation_config = fed_config

    gateway = _load_gateway_config(config_path, _federation_config)
    gateway_config = _create_gateway_config(gateway)

    _proxy_instance = StargateProxy(
        gateway_config=gateway_config, config_path=config_path
    )
    logger.info(f"StargateProxy initialized with config: {config_path}")

    return _proxy_instance


def get_proxy() -> StargateProxy:
    """
    Get the global proxy instance (FastAPI dependency).

    Must be initialized via init_proxy() during app startup.
    No parameters to avoid FastAPI treating Pydantic models as body params.
    """
    if _proxy_instance is None:
        raise RuntimeError(
            "Proxy not initialized. Call init_proxy() during app startup."
        )
    return _proxy_instance


def _load_gateway_config(
    config_path: str,
    fed_config: FederationConfig,
) -> dict[str, Any]:
    """
    Load gateway configuration.

    Args:
        config_path: Path to stargate config file
        fed_config: Pre-loaded FederationConfig (no longer loads independently)

    Returns:
        Gateway config dict or empty dict for router-only modes
    """
    from gateways.config_loader import load_gateway_config_sync

    fed_mode = fed_config.mode.value

    # ─── REMOTE WITH LOCAL EDGE (RELAY) ─────────────────────────────────
    if fed_mode == "remote" and fed_config.local_edge:
        edge_id = fed_config.local_edge.stargate_id
        logger.info(
            f"Relay topology (mode=remote, local_edge={edge_id}): "
            f"Router-only mode (no local Gateway)"
        )
        return {}

    # ─── REMOTE WITH LOCAL GATEWAY ──────────────────────────────────────
    if fed_mode == "remote":
        logger.info("Relay with gateway section: execution-capable mode")

    # ─── EDGE/MASTER/STANDALONE ─────────────────────────────────────────
    try:
        gateway = load_gateway_config_sync()
    except Exception as e:
        logger.info(f"No gateway config loaded (router-only Master mode): {e}")
        gateway = {}

    logger.info(
        f"_load_gateway_config(): mode={fed_mode}, "
        f"url={gateway.get('url')}, "
        f"socket_path={gateway.get('socket_path')}, "
        f"base_url={gateway.get('base_url')}"
    )

    return gateway


def _create_gateway_config(gw: dict[str, Any]) -> GatewayConfig | None:
    """
    Create GatewayConfig from gateway dictionary.

    Returns None for router-only Master (empty dict, url: null, no socket_path).
    """
    # Router-only Master: empty gateway config
    if not gw:
        logger.info(
            "Router-only mode: No local gateway configured (empty gateway dict)"
        )
        return None

    socket_path = gw.get("socket_path")
    base_url = gw.get("url", gw.get("base_url"))

    # Router-only Master: no gateway config
    if base_url is None and socket_path is None:
        logger.info("Router-only mode: No local gateway configured")
        return None

    # Parse unix:// URLs and extract socket path
    if base_url and base_url.startswith("unix://"):
        socket_path = base_url[7:]  # Remove "unix://" prefix
        base_url = "http://localhost"  # Required but ignored for Unix sockets
        logger.info(f"Parsed unix:// URL: {gw.get('url')} -> socket_path={socket_path}")

    # Convert container socket paths to host paths
    # Docker volume mount: /tmp/universal-sockets:/sockets
    # Container: /sockets/gateway.sock -> Host: /tmp/universal-sockets/gateway.sock
    if socket_path and socket_path.startswith("/sockets/"):
        original_path = socket_path
        socket_name = socket_path.split("/")[-1]
        socket_path = f"/tmp/universal-sockets/{socket_name}"
        logger.info(
            f"Converted container path to host path: {original_path} -> {socket_path}"
        )

    # If socket_path is set, base_url is optional (default to localhost)
    if socket_path and not base_url:
        base_url = "http://localhost"

    gateway_config = GatewayConfig(
        base_url=base_url,
        name=gw.get("name", base_url or "master"),
        enabled=gw.get("enabled", True),
        api_key=gw.get("api_key"),
        timeout=gw.get("timeout", 30.0),
        connectivity_timeout=gw.get("connectivity_timeout"),
        health_timeout=gw.get("health_timeout"),
        capabilities=gw.get("capabilities"),
        socket_path=socket_path,
    )

    # CRITICAL: Validate socket_path is set if URL was unix://
    original_url = gw.get("url", gw.get("base_url", ""))
    if original_url.startswith("unix://"):
        if not gateway_config.socket_path:
            error_msg = (
                f"CRITICAL: Parsed unix:// URL but socket_path is None! "
                f"URL: {original_url}, socket_path: {gateway_config.socket_path}, "
                f"base_url: {base_url}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

    # Diagnostic logging
    logger.info(
        f"Created GatewayConfig: name={gateway_config.name}, "
        f"socket_path={gateway_config.socket_path}, base_url={gateway_config.base_url}"
    )

    return gateway_config


async def get_auth_dependency(request: Request) -> dict:
    """Get authorization dependency that properly receives Request parameter"""
    proxy = get_proxy()
    auth_func = proxy.authorization_manager.get_auth_dependency()
    return await auth_func(request)


async def get_optional_auth_dependency(request: Request) -> dict:
    """Get optional authorization dependency that properly receives Request parameter"""
    proxy = get_proxy()
    auth_func = proxy.authorization_manager.get_optional_auth_dependency()
    return await auth_func(request)
