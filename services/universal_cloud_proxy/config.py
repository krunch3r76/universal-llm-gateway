"""
Cloud proxy configuration — loads ~/.gateway/cloud-proxy.yaml.

Each provider entry specifies an API key env var, base URL, concurrency
limits, and optional model prefix filters.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".gateway" / "cloud-proxy.yaml"

DEFAULT_PORT = 8200
DEFAULT_HOST = "127.0.0.1"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MAX_CONCURRENT = 5
DEFAULT_REFRESH_INTERVAL_HOURS = 6


@dataclass(slots=True, kw_only=True)
class ProviderConfig:
    """Validated configuration for a single cloud provider."""

    provider: str
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    refresh_interval_hours: int = DEFAULT_REFRESH_INTERVAL_HOURS
    allow_prefixes: list[str] = field(default_factory=list)


DEFAULT_STARGATE_URL = "http://localhost:9999"


DEFAULT_SOCKET_PATH = "/tmp/universal-protocol/cloud-proxy.sock"


@dataclass(slots=True, kw_only=True)
class CloudProxyConfig:
    """Top-level cloud proxy configuration."""

    port: int = DEFAULT_PORT
    host: str = DEFAULT_HOST
    socket_path: str | None = None
    providers: list[ProviderConfig] = field(default_factory=list)
    stargate_url: str = DEFAULT_STARGATE_URL


def _validate_base_url(provider: str, url: str) -> None:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError(
            f"providers[{provider}].base_url must be an https:// URL, got: {url!r}"
        )


def _validate_allow_prefixes(provider: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"providers[{provider}].allow_prefixes must be a list, "
            f"got: {type(value).__name__}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ValueError(
                f"providers[{provider}].allow_prefixes[{i}] must be a "
                f"non-empty string, got: {item!r}"
            )
    return value


def _parse_provider(entry: dict[str, Any]) -> ProviderConfig | None:
    """Parse and validate a single provider entry. Returns None if skipped."""
    if not isinstance(entry, dict):
        raise ValueError(
            f"Provider entry must be a mapping, got {type(entry).__name__}"
        )

    provider = entry.get("provider")
    if not provider or not isinstance(provider, str):
        raise ValueError("Provider entry missing 'provider' string")

    raw_key = entry.get("api_key")
    if isinstance(raw_key, str) and raw_key.strip():
        api_key = raw_key.strip()
    else:
        api_key_env = entry.get("api_key_env", "")
        if not api_key_env or not isinstance(api_key_env, str):
            raise ValueError(
                f"providers[{provider}] must set 'api_key' (literal) or 'api_key_env' (env var name)"
            )
        api_key = os.environ.get(api_key_env, "").strip()

    if not api_key:
        logger.warning(
            "Provider '%s' skipped: no api_key in config and env var %s is not set",
            provider,
            entry.get("api_key_env", "?"),
        )
        return None

    max_concurrent = entry.get("max_concurrent", DEFAULT_MAX_CONCURRENT)
    if not isinstance(max_concurrent, int) or max_concurrent < 1:
        raise ValueError(
            f"providers[{provider}].max_concurrent must be a positive integer, "
            f"got: {max_concurrent}"
        )

    refresh_interval_hours = entry.get(
        "refresh_interval_hours", DEFAULT_REFRESH_INTERVAL_HOURS
    )
    if not isinstance(refresh_interval_hours, int) or refresh_interval_hours < 1:
        raise ValueError(
            f"providers[{provider}].refresh_interval_hours must be a positive integer, "
            f"got: {refresh_interval_hours}"
        )

    base_url = entry.get("base_url", DEFAULT_BASE_URL)
    _validate_base_url(provider, base_url)

    allow_prefixes = _validate_allow_prefixes(provider, entry.get("allow_prefixes", []))

    return ProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        max_concurrent=max_concurrent,
        refresh_interval_hours=refresh_interval_hours,
        allow_prefixes=allow_prefixes,
    )


def load_config(config_path: Path | None = None) -> CloudProxyConfig:
    """Load cloud proxy config from YAML file.

    Returns a config with empty providers list if the file is missing
    or contains no valid providers.
    """
    path = config_path or _CONFIG_PATH
    if not path.exists():
        logger.warning("Cloud proxy config not found: %s", path)
        return CloudProxyConfig()

    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.error("Failed to parse cloud proxy config: %s", path, exc_info=True)
        return CloudProxyConfig()

    if not isinstance(loaded, dict):
        logger.error("Invalid cloud proxy config root type: expected mapping")
        return CloudProxyConfig()

    raw: dict[str, Any] = loaded

    socket_path_val = raw.get("socket_path")
    socket_path: str | None = None
    if socket_path_val is not None:
        if isinstance(socket_path_val, str) and socket_path_val.strip():
            socket_path = socket_path_val.strip()
        else:
            logger.error(
                "Invalid socket_path in cloud proxy config: %r", socket_path_val
            )

    host_val = raw.get("host")
    port_val = raw.get("port")
    has_tcp = (
        host_val is not None
        and isinstance(host_val, str)
        and host_val.strip()
        and port_val is not None
    )
    try:
        has_tcp = (
            has_tcp
            and isinstance(port_val, int | float)
            and 1 <= int(port_val) <= 65535
        )
    except (TypeError, ValueError):
        has_tcp = False
    if socket_path and has_tcp:
        raise ValueError(
            "cloud-proxy.yaml: cannot set both socket_path and host+port; "
            "use one transport mode"
        )

    port = DEFAULT_PORT
    if port_val is not None:
        try:
            candidate = int(port_val)
            if 1 <= candidate <= 65535:
                port = candidate
            else:
                logger.error(
                    "Invalid port in cloud proxy config: %r (out of range 1-65535), using default",
                    port_val,
                )
        except (TypeError, ValueError):
            logger.error(
                "Invalid port in cloud proxy config: %r (not an integer), using default",
                port_val,
            )

    host = DEFAULT_HOST
    if host_val and isinstance(host_val, str) and host_val.strip():
        host = host_val.strip()

    stargate_url = raw.get("stargate_url", DEFAULT_STARGATE_URL)
    if not isinstance(stargate_url, str) or not stargate_url.strip():
        stargate_url = DEFAULT_STARGATE_URL

    raw_providers = raw.get("providers", [])
    if not isinstance(raw_providers, list):
        logger.error("providers must be a list")
        return CloudProxyConfig(
            port=port, host=host, socket_path=socket_path, stargate_url=stargate_url
        )

    providers: list[ProviderConfig] = []
    for entry in raw_providers:
        parsed = _parse_provider(entry)
        if parsed is not None:
            providers.append(parsed)

    return CloudProxyConfig(
        port=port,
        host=host,
        socket_path=socket_path,
        providers=providers,
        stargate_url=stargate_url,
    )
