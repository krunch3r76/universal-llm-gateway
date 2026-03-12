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
    native_tools: list[str] = field(default_factory=list)
    mcp_server_url: str | None = None
    mcp_auth_token: str | None = None
    mcp_v2: bool = False


DEFAULT_STARGATE_URL = "http://localhost:9999"


DEFAULT_SOCKET_PATH = "/tmp/universal-protocol/cloud-proxy.sock"


def _default_base_url(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "anthropic":
        return "https://api.anthropic.com/v1"
    if normalized == "openai":
        return "https://api.openai.com/v1"
    return DEFAULT_BASE_URL


def _normalized_provider(value: str) -> str:
    provider = value.strip().lower()
    if not provider:
        raise ValueError("providers[].provider must be non-empty")
    return provider


@dataclass(slots=True, kw_only=True)
class CloudProxyConfig:
    """Top-level cloud proxy configuration."""

    port: int = DEFAULT_PORT
    host: str = DEFAULT_HOST
    socket_path: str | None = None
    providers: list[ProviderConfig] = field(default_factory=list)
    stargate_url: str = DEFAULT_STARGATE_URL


def _validate_base_url(provider: str, url: str) -> None:
    if not isinstance(url, str):
        raise ValueError(
            f"providers[{provider}].base_url must be a URL string, got: {url!r}"
        )
    if url.startswith("https://"):
        return
    is_local_http = url.startswith("http://localhost") or url.startswith(
        "http://127.0.0.1"
    )
    if not is_local_http:
        raise ValueError(
            f"providers[{provider}].base_url must be an https:// URL "
            f"(or local http://localhost/127.0.0.1), got: {url!r}"
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

    raw_provider = entry.get("provider")
    if not isinstance(raw_provider, str):
        raise ValueError("Provider entry missing 'provider' string")
    provider = _normalized_provider(raw_provider)

    api_key = ""
    raw_key = entry.get("api_key")
    if raw_key is not None:
        api_key = str(raw_key).strip()
    if not api_key:
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

    base_url = entry.get("base_url", _default_base_url(provider))
    _validate_base_url(provider, base_url)

    allow_prefixes = _validate_allow_prefixes(provider, entry.get("allow_prefixes", []))

    native_tools: list[str] = []
    raw_native = entry.get("native_tools")
    if isinstance(raw_native, list):
        native_tools = [str(t) for t in raw_native if isinstance(t, str) and t.strip()]

    mcp_server_url: str | None = None
    raw_mcp_url = entry.get("mcp_server_url")
    if raw_mcp_url is not None:
        if isinstance(raw_mcp_url, str) and raw_mcp_url.strip():
            mcp_server_url = raw_mcp_url.strip()
        else:
            logger.error(
                "providers[%s].mcp_server_url must be a non-empty string, got: %r",
                provider,
                raw_mcp_url,
            )

    mcp_auth_token: str | None = None
    raw_mcp_token = entry.get("mcp_auth_token")
    if raw_mcp_token is not None:
        if isinstance(raw_mcp_token, str) and raw_mcp_token.strip():
            mcp_auth_token = raw_mcp_token.strip()
        else:
            logger.error(
                "providers[%s].mcp_auth_token must be a non-empty string, got: %r",
                provider,
                raw_mcp_token,
            )

    mcp_v2 = bool(entry.get("mcp_v2", False))

    return ProviderConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        max_concurrent=max_concurrent,
        refresh_interval_hours=refresh_interval_hours,
        allow_prefixes=allow_prefixes,
        native_tools=native_tools,
        mcp_server_url=mcp_server_url,
        mcp_auth_token=mcp_auth_token,
        mcp_v2=mcp_v2,
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
    has_tcp = False
    if (
        host_val is not None
        and isinstance(host_val, str)
        and host_val.strip()
        and port_val is not None
    ):
        try:
            tcp_port = int(str(port_val).strip())
            has_tcp = 1 <= tcp_port <= 65535
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

    stargate_url = DEFAULT_STARGATE_URL
    stargate_url_val = raw.get("stargate_url")
    if stargate_url_val is not None:
        if isinstance(stargate_url_val, str) and stargate_url_val.strip():
            stargate_url = stargate_url_val.strip()
        else:
            logger.error(
                "Invalid stargate_url in cloud proxy config: %r, using default",
                stargate_url_val,
            )

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
