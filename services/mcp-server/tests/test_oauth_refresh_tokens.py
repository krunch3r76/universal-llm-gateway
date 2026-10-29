"""Refresh-token grant: metadata, code exchange, rotation, reuse revocation."""

from __future__ import annotations

import base64
import hashlib
import secrets

import pytest
from oauth_config import OAuthClientConfig, OAuthServerConfig
from oauth_service import OAuthError, OAuthService
from oauth_store import OAuthStore


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def _service(*, clients: list[OAuthClientConfig] | None = None) -> OAuthService:
    return OAuthService(
        config=OAuthServerConfig(
            issuer="https://mcp.k-1.me",
            resource_server_url="https://mcp.k-1.me/mcp",
            supported_scopes=["mcp"],
            dynamic_client_redirect_hosts=["grok.com"],
            clients=clients or [],
        ),
        store=OAuthStore(),
    )


def _code_exchange_tokens(
    svc: OAuthService,
    *,
    client_id: str,
    redirect_uri: str,
    client_secret: str | None = None,
) -> dict[str, object]:
    verifier, challenge = _pkce_pair()
    auth_req = svc.validate_authorization_request(
        response_type="code",
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope="mcp",
        state=None,
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    code = svc.issue_authorization_code(auth_req)
    exchange_req = svc.validate_token_exchange(
        grant_type="authorization_code",
        code=code,
        redirect_uri=redirect_uri,
        client_id=client_id,
        code_verifier=verifier,
        client_secret=client_secret,
    )
    return svc.exchange_authorization_code(exchange_req)


def test_authorization_metadata_lists_refresh_token_grant() -> None:
    metadata = _service().build_authorization_server_metadata()
    assert metadata["grant_types_supported"] == [
        "authorization_code",
        "refresh_token",
    ]


def test_code_exchange_includes_refresh_token() -> None:
    svc = _service(
        clients=[
            OAuthClientConfig(
                client_id="pub-client",
                redirect_uris=["https://grok.com/cb"],
            )
        ]
    )
    body = _code_exchange_tokens(
        svc,
        client_id="pub-client",
        redirect_uri="https://grok.com/cb",
    )
    assert isinstance(body.get("refresh_token"), str)
    assert len(str(body["refresh_token"])) >= 16
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 604_800


def test_refresh_grant_rotates_tokens() -> None:
    svc = _service(
        clients=[
            OAuthClientConfig(
                client_id="pub-client",
                redirect_uris=["https://grok.com/cb"],
            )
        ]
    )
    initial = _code_exchange_tokens(
        svc,
        client_id="pub-client",
        redirect_uri="https://grok.com/cb",
    )
    refresh_req = svc.validate_refresh_exchange(
        grant_type="refresh_token",
        refresh_token=str(initial["refresh_token"]),
        client_id="pub-client",
        client_secret=None,
    )
    rotated = svc.exchange_refresh_token(refresh_req)

    assert rotated["access_token"] != initial["access_token"]
    assert rotated["refresh_token"] != initial["refresh_token"]
    assert rotated["scope"] == initial["scope"]
    assert svc.validate_access_token(str(rotated["access_token"])) is not None


def test_refresh_token_reuse_revokes_family() -> None:
    svc = _service(
        clients=[
            OAuthClientConfig(
                client_id="pub-client",
                redirect_uris=["https://grok.com/cb"],
            )
        ]
    )
    initial = _code_exchange_tokens(
        svc,
        client_id="pub-client",
        redirect_uri="https://grok.com/cb",
    )
    old_refresh = str(initial["refresh_token"])
    refresh_req = svc.validate_refresh_exchange(
        grant_type="refresh_token",
        refresh_token=old_refresh,
        client_id="pub-client",
        client_secret=None,
    )
    rotated = svc.exchange_refresh_token(refresh_req)
    new_refresh = str(rotated["refresh_token"])

    with pytest.raises(OAuthError, match="already been used"):
        svc.exchange_refresh_token(
            svc.validate_refresh_exchange(
                grant_type="refresh_token",
                refresh_token=old_refresh,
                client_id="pub-client",
                client_secret=None,
            )
        )

    with pytest.raises(OAuthError, match="invalid or expired"):
        svc.exchange_refresh_token(
            svc.validate_refresh_exchange(
                grant_type="refresh_token",
                refresh_token=new_refresh,
                client_id="pub-client",
                client_secret=None,
            )
        )
    assert svc.validate_access_token(str(rotated["access_token"])) is None


def test_refresh_rejects_wrong_client_secret_for_confidential_client() -> None:
    svc = _service()
    registration = svc.register_dynamic_client(
        {
            "redirect_uris": ["https://grok.com/connectors/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        }
    )
    client_id = str(registration["client_id"])
    client_secret = str(registration["client_secret"])
    redirect_uri = "https://grok.com/connectors/oauth/callback"

    initial = _code_exchange_tokens(
        svc,
        client_id=client_id,
        redirect_uri=redirect_uri,
        client_secret=client_secret,
    )

    with pytest.raises(OAuthError, match="client_secret mismatch"):
        svc.validate_refresh_exchange(
            grant_type="refresh_token",
            refresh_token=str(initial["refresh_token"]),
            client_id=client_id,
            client_secret="wrong-secret",
        )
