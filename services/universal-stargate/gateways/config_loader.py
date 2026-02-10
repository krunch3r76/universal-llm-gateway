"""Async configuration loading for gateways."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import yaml
from universal_logging import get_logger

logger = get_logger(__name__)


def load_stargate_config_sync() -> dict[str, Any]:
    """Load stargate configuration from stargate_config.yaml (sync)."""
    env_config_path = os.environ.get("STARGATE_CONFIG")
    if env_config_path:
        config_path = env_config_path
        logger.info(f"Using config from STARGATE_CONFIG env: {config_path}")
    else:
        # Default to project root config (consistent with other modules)
        config_path = "config/stargate_config.yaml"
        logger.debug(f"Using default config path: {config_path}")

    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load stargate config from {config_path}: {e}")
        raise


def load_gateway_config_sync() -> dict[str, Any]:
    """
    Load single gateway configuration from stargate_config.yaml (sync).

    Returns:
        Gateway config dict from gateway: section

    Raises:
        ConfigurationError: If config is invalid
    """
    from .config_validation import derive_token_endpoint, validate_gateway_config

    config = load_stargate_config_sync()
    validate_gateway_config(config)

    gateway_cfg = config["gateway"]

    # Auto-derive token endpoint if not explicitly set
    token_mgmt = config.setdefault("token_management", {})
    if "gateway_endpoint" not in token_mgmt:
        token_mgmt["gateway_endpoint"] = derive_token_endpoint(config)

    return gateway_cfg


def load_proxy_config_sync() -> dict[str, Any]:
    """Load proxy configuration from proxy.yaml (sync)."""
    config_path = "config/proxy.yaml"
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load proxy config: {e}")
        return {"request_queue": {}}


async def load_gateway_config() -> dict[str, Any]:
    """
    Load single gateway configuration asynchronously (non-blocking).

    Returns:
        Gateway config dict from gateway: section

    Raises:
        ConfigurationError: If config is invalid
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, load_gateway_config_sync)


async def load_proxy_config() -> dict[str, Any]:
    """Load proxy configuration asynchronously (non-blocking)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, load_proxy_config_sync)
