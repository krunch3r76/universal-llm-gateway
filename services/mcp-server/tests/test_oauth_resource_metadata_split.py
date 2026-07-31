"""Path-aware OAuth protected-resource metadata for /mcp/life and /mcp/code."""

from __future__ import annotations

from oauth_config import OAuthServerConfig
from oauth_service import OAuthService
from oauth_store import OAuthStore


def _service() -> OAuthService:
    config = OAuthServerConfig(
        issuer="https://mcp.k-1.me",
        resource_server_url="https://mcp.k-1.me/mcp/code",
        supported_scopes=["mcp"],
        dynamic_client_redirect_hosts=["grok.com"],
        clients=[],
        enabled=True,
    )
    return OAuthService(config=config, store=OAuthStore(db_path=":memory:"))


def test_default_metadata_uses_configured_resource() -> None:
    meta = _service().build_protected_resource_metadata()
    assert meta["resource"] == "https://mcp.k-1.me/mcp/code"


def test_path_suffix_selects_life_and_code_resources() -> None:
    svc = _service()
    life = svc.build_protected_resource_metadata(resource_path="mcp/life")
    code = svc.build_protected_resource_metadata(resource_path="mcp/code")
    assert life["resource"] == "https://mcp.k-1.me/mcp/life"
    assert code["resource"] == "https://mcp.k-1.me/mcp/code"


def test_www_authenticate_metadata_url_is_path_aware() -> None:
    svc = _service()
    assert (
        svc.resource_metadata_url_for_request_path("/mcp/life")
        == "https://mcp.k-1.me/.well-known/oauth-protected-resource/mcp/life"
    )
    assert (
        svc.resource_metadata_url_for_request_path("/mcp/code")
        == "https://mcp.k-1.me/.well-known/oauth-protected-resource/mcp/code"
    )
    assert (
        svc.resource_metadata_url_for_request_path("/health")
        == "https://mcp.k-1.me/.well-known/oauth-protected-resource"
    )
