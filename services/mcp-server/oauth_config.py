"""OAuth 2.1 server configuration loader for the MCP server.

Reads the ``oauth:`` block from ``~/.gateway/mcp.yaml`` with env-var overrides
(``MCP_OAUTH_ENABLED``, ``MCP_OAUTH_ISSUER``, ``MCP_RESOURCE_SERVER_URL``).
Returns ``OAuthServerConfig`` when enabled with a valid issuer, or ``None``
when disabled or misconfigured.  Exits hard on an insecure (non-HTTPS) issuer
to prevent accidental production token leakage.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(
    os.getenv("MCP_CONFIG_PATH", str(Path.home() / ".gateway" / "mcp.yaml"))
)


@dataclass(slots=True, kw_only=True)
class OAuthClientConfig:
    """Single registered OAuth client as declared in operator config."""

    client_id: str
    client_secret: str | None = None
    redirect_uris: list[str]


@dataclass(slots=True, kw_only=True)
class OAuthServerConfig:
    """Top-level OAuth server settings resolved from YAML + env overrides."""

    issuer: str
    resource_server_url: str
    authorize_path: str = "/oauth/authorize"
    token_path: str = "/oauth/token"
    resource_metadata_path: str = "/.well-known/oauth-protected-resource"
    authorization_metadata_path: str = "/.well-known/oauth-authorization-server"
    supported_scopes: list[str] | None = None
    clients: list[OAuthClientConfig] | None = None
    enabled: bool = True


def _read_yaml_config() -> dict[str, object]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        loaded: object = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.error("Failed to parse MCP config: %s", _CONFIG_PATH, exc_info=True)
        return {}
    if not isinstance(loaded, dict):
        logger.error("Invalid MCP config root type at %s", _CONFIG_PATH)
        return {}
    return loaded


def _parse_client(entry: object) -> OAuthClientConfig | None:
    if not isinstance(entry, dict):
        logger.warning("Skipping invalid oauth client entry: %r", entry)
        return None
    raw_client_id = entry.get("client_id")
    raw_redirect_uris = entry.get("redirect_uris")
    raw_client_secret = entry.get("client_secret")
    if not isinstance(raw_client_id, str) or not raw_client_id.strip():
        logger.warning(
            "Skipping oauth client with invalid client_id: %r", raw_client_id
        )
        return None
    if not isinstance(raw_redirect_uris, list):
        logger.warning(
            "Skipping oauth client %s: redirect_uris must be a list", raw_client_id
        )
        return None
    redirect_uris = [
        value.strip()
        for value in raw_redirect_uris
        if isinstance(value, str) and value.strip()
    ]
    if not redirect_uris:
        logger.warning("Skipping oauth client %s: redirect_uris empty", raw_client_id)
        return None
    client_secret: str | None = None
    if isinstance(raw_client_secret, str) and raw_client_secret.strip():
        client_secret = raw_client_secret.strip()
    return OAuthClientConfig(
        client_id=raw_client_id.strip(),
        client_secret=client_secret,
        redirect_uris=redirect_uris,
    )


def load_oauth_config() -> OAuthServerConfig | None:
    """Load OAuth config from ``~/.gateway/mcp.yaml`` with env-var overrides.

    Returns ``OAuthServerConfig`` when OAuth is enabled and the issuer is a
    valid HTTPS URL.  Returns ``None`` when explicitly disabled or when no
    issuer is configured.  Calls ``sys.exit(1)`` if the issuer is present
    but not HTTPS — insecure issuers are a hard config error, not a fallback.
    """
    oauth_enabled = os.getenv("MCP_OAUTH_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not oauth_enabled:
        logger.info("OAuth disabled via MCP_OAUTH_ENABLED=false")
        return None

    config_root = _read_yaml_config()
    oauth_block: dict[str, object] = {}
    raw_oauth = config_root.get("oauth")
    if isinstance(raw_oauth, dict):
        oauth_block = raw_oauth

    issuer = os.getenv("MCP_OAUTH_ISSUER", "").strip()
    if not issuer:
        configured = oauth_block.get("issuer")
        if isinstance(configured, str) and configured.strip():
            issuer = configured.strip()
    if not issuer:
        logger.warning("OAuth disabled: missing oauth.issuer or MCP_OAUTH_ISSUER")
        return None

    if not issuer.startswith("https://"):
        logger.error("OAuth issuer must use https:// — got %s", issuer)
        sys.exit(1)

    resource_server_url = os.getenv("MCP_RESOURCE_SERVER_URL", "").strip() or issuer

    raw_scopes = oauth_block.get("scopes", ["mcp"])
    supported_scopes: list[str] = []
    if isinstance(raw_scopes, list):
        supported_scopes = [
            v.strip() for v in raw_scopes if isinstance(v, str) and v.strip()
        ]
    if not supported_scopes:
        supported_scopes = ["mcp"]

    clients: list[OAuthClientConfig] = []
    raw_clients = oauth_block.get("clients", [])
    if isinstance(raw_clients, list):
        for entry in raw_clients:
            parsed = _parse_client(entry)
            if parsed is not None:
                clients.append(parsed)

    return OAuthServerConfig(
        issuer=issuer.rstrip("/"),
        resource_server_url=resource_server_url.rstrip("/"),
        supported_scopes=supported_scopes,
        clients=clients,
        enabled=True,
    )
