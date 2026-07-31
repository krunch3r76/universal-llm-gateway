"""OAuth dynamic client registration tests."""

from __future__ import annotations

import pytest
from oauth_config import OAuthServerConfig
from oauth_service import OAuthError, OAuthService
from oauth_store import OAuthStore


def _service() -> OAuthService:
    return OAuthService(
        config=OAuthServerConfig(
            issuer="https://mcp.k-1.me",
            resource_server_url="https://mcp.k-1.me/mcp",
            supported_scopes=["mcp"],
            dynamic_client_redirect_hosts=["grok.com", "*.grok.com"],
            clients=[],
        ),
        store=OAuthStore(),
    )


def test_authorization_metadata_advertises_registration_endpoint() -> None:
    metadata = _service().build_authorization_server_metadata()

    assert metadata["registration_endpoint"] == "https://mcp.k-1.me/oauth/register"


def test_protected_resource_metadata_uses_mcp_endpoint_resource() -> None:
    metadata = _service().build_protected_resource_metadata()

    assert metadata["resource"] == "https://mcp.k-1.me/mcp"


def test_protected_resource_metadata_path_scoped_life() -> None:
    metadata = _service().build_protected_resource_metadata(resource_path="mcp/life")

    assert metadata["resource"] == "https://mcp.k-1.me/mcp/life"


def test_resource_metadata_url_for_code() -> None:
    assert (
        _service().resource_metadata_url_for("/mcp/code")
        == "https://mcp.k-1.me/.well-known/oauth-protected-resource/mcp/code"
    )


def test_dynamic_registration_accepts_client_secret_post() -> None:
    """Claude picks client_secret_post when AS advertises it — must succeed."""
    svc = OAuthService(
        config=OAuthServerConfig(
            issuer="https://mcp.k-1.me",
            resource_server_url="https://mcp.k-1.me/mcp/life",
            supported_scopes=["mcp"],
            dynamic_client_redirect_hosts=["claude.ai", "claude.com"],
            clients=[],
        ),
        store=OAuthStore(),
    )
    registration = svc.register_dynamic_client(
        {
            "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        }
    )

    assert str(registration["client_id"]).startswith("dyn-")
    assert registration["token_endpoint_auth_method"] == "client_secret_post"
    assert isinstance(registration.get("client_secret"), str)
    assert len(str(registration["client_secret"])) >= 16


def test_dynamic_registration_rejects_unsupported_auth_method() -> None:
    with pytest.raises(OAuthError, match="none or client_secret_post"):
        _service().register_dynamic_client(
            {
                "redirect_uris": ["https://grok.com/connectors/oauth/callback"],
                "token_endpoint_auth_method": "client_secret_basic",
            }
        )


def test_dynamic_registration_accepts_grok_redirect_uri() -> None:
    registration = _service().register_dynamic_client(
        {
            "redirect_uris": ["https://grok.com/connectors/oauth/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )

    assert str(registration["client_id"]).startswith("dyn-")
    assert registration["token_endpoint_auth_method"] == "none"


def test_dynamic_registration_accepts_optional_refresh_token_grant() -> None:
    registration = _service().register_dynamic_client(
        {
            "redirect_uris": ["https://grok.com/connectors/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
    )

    assert str(registration["client_id"]).startswith("dyn-")
    assert registration["grant_types"] == ["authorization_code"]


def test_dynamic_registration_rejects_refresh_token_without_authorization_code() -> (
    None
):
    with pytest.raises(OAuthError, match="authorization_code grant_type is required"):
        _service().register_dynamic_client(
            {
                "redirect_uris": ["https://grok.com/connectors/oauth/callback"],
                "grant_types": ["refresh_token"],
            }
        )


def test_dynamic_registration_rejects_untrusted_redirect_host() -> None:
    with pytest.raises(OAuthError, match="dynamic redirect_uri host is not allowed"):
        _service().register_dynamic_client(
            {"redirect_uris": ["https://attacker.example/callback"]}
        )
