"""OAuth 2.1 authorization service — validation, code issuance, and token exchange.

Encapsulates the full OAuth server logic: metadata builders, PKCE S256
verification, authorization request validation, code issuance with consent,
token exchange (validate-before-consume ordering per reviewer correction),
and bearer token admission.  The store is injected so the service stays
testable without I/O.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from mcp_events import record
from oauth_config import OAuthServerConfig
from oauth_models import AccessTokenRecord, AuthorizationCodeRecord, RegisteredClient
from oauth_store import OAuthStore

logger = logging.getLogger(__name__)

_AUTHORIZATION_CODE_TTL_SECONDS = 300
_ACCESS_TOKEN_TTL_SECONDS = 3600


@dataclass(slots=True, kw_only=True)
class AuthorizationRequest:
    """Validated authorization request ready for code issuance."""

    response_type: str
    client_id: str
    redirect_uri: str
    scope: str
    state: str | None
    code_challenge: str
    code_challenge_method: str


@dataclass(slots=True, kw_only=True)
class TokenExchangeRequest:
    """Validated token exchange request ready for code consumption."""

    grant_type: str
    code: str
    redirect_uri: str
    client_id: str
    code_verifier: str
    client_secret: str | None


class OAuthError(ValueError):
    """OAuth-specific error carrying RFC 6749 error code and description."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


class OAuthService:
    """OAuth 2.1 authorization server backed by in-memory store and YAML config.

    Loads registered clients from config at init time. Provides metadata
    endpoints, authorization validation, PKCE-bound code issuance, and
    token exchange with validate-before-consume ordering.
    """

    def __init__(self, *, config: OAuthServerConfig, store: OAuthStore) -> None:
        self._config = config
        self._store = store
        for client_cfg in config.clients or []:
            self._store.save_client(
                RegisteredClient(
                    client_id=client_cfg.client_id,
                    client_secret=client_cfg.client_secret,
                    redirect_uris=client_cfg.redirect_uris,
                )
            )

    @property
    def issuer(self) -> str:
        return self._config.issuer

    @property
    def resource_metadata_url(self) -> str:
        return f"{self._config.issuer}{self._config.resource_metadata_path}"

    @property
    def authorization_endpoint(self) -> str:
        return f"{self._config.issuer}{self._config.authorize_path}"

    @property
    def token_endpoint(self) -> str:
        return f"{self._config.issuer}{self._config.token_path}"

    def build_protected_resource_metadata(self) -> dict[str, object]:
        return {
            "resource": self._config.resource_server_url,
            "authorization_servers": [self._config.issuer],
            "bearer_methods_supported": ["header"],
        }

    def build_authorization_server_metadata(self) -> dict[str, object]:
        return {
            "issuer": self._config.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": self._config.supported_scopes or ["mcp"],
        }

    def validate_authorization_request(
        self,
        *,
        response_type: str,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str | None,
        code_challenge: str,
        code_challenge_method: str,
    ) -> AuthorizationRequest:
        if response_type != "code":
            raise OAuthError("unsupported_response_type", "response_type must be code")
        client = self._store.fetch_client(client_id)
        if client is None:
            raise OAuthError("unauthorized_client", "Unknown client_id")
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri is not registered")
        if not scope.strip():
            scope = "mcp"
        requested = {v for v in scope.split() if v}
        supported = set(self._config.supported_scopes or ["mcp"])
        if not requested.issubset(supported):
            raise OAuthError("invalid_scope", "Requested scope not supported")
        if not code_challenge.strip():
            raise OAuthError("invalid_request", "code_challenge is required")
        if code_challenge_method != "S256":
            raise OAuthError("invalid_request", "code_challenge_method must be S256")

        record("mcp.oauth.authorization.validated", client_id=client_id, scope=scope)
        return AuthorizationRequest(
            response_type=response_type,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )

    def issue_authorization_code(self, request: AuthorizationRequest) -> str:
        self._store.purge_expired()
        code = secrets.token_urlsafe(32)
        self._store.save_authorization_code(
            AuthorizationCodeRecord(
                code=code,
                client_id=request.client_id,
                redirect_uri=request.redirect_uri,
                code_challenge=request.code_challenge,
                code_challenge_method=request.code_challenge_method,
                scope=request.scope,
                expires_at=time.time() + _AUTHORIZATION_CODE_TTL_SECONDS,
            )
        )
        record(
            "mcp.oauth.code.issued",
            client_id=request.client_id,
            scope=request.scope,
            ttl_seconds=_AUTHORIZATION_CODE_TTL_SECONDS,
        )
        return code

    def build_redirect_uri(
        self,
        *,
        redirect_uri: str,
        code: str,
        state: str | None,
    ) -> str:
        params: dict[str, str] = {"code": code}
        if state:
            params["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{sep}{urlencode(params)}"

    def validate_token_exchange(
        self,
        *,
        grant_type: str,
        code: str,
        redirect_uri: str,
        client_id: str,
        code_verifier: str,
        client_secret: str | None,
    ) -> TokenExchangeRequest:
        if grant_type != "authorization_code":
            raise OAuthError(
                "unsupported_grant_type", "grant_type must be authorization_code"
            )
        if not code_verifier.strip():
            raise OAuthError("invalid_request", "code_verifier is required")
        client = self._store.fetch_client(client_id)
        if client is None:
            raise OAuthError("invalid_client", "Unknown client_id")
        match client.client_secret:
            case None:
                pass
            case expected if client_secret == expected:
                pass
            case _:
                raise OAuthError("invalid_client", "client_secret mismatch")
        return TokenExchangeRequest(
            grant_type=grant_type,
            code=code,
            redirect_uri=redirect_uri,
            client_id=client_id,
            code_verifier=code_verifier,
            client_secret=client_secret,
        )

    def exchange_authorization_code(
        self, request: TokenExchangeRequest
    ) -> dict[str, object]:
        self._store.purge_expired()

        code_record = self._store.fetch_authorization_code(request.code)
        if code_record is None:
            self._store.purge_expired()
            record(
                "mcp.oauth.token.exchange.failed",
                client_id=request.client_id,
                reason="invalid_or_expired_code",
            )
            raise OAuthError(
                "invalid_grant", "Authorization code is invalid or expired"
            )

        if code_record.client_id != request.client_id:
            record(
                "mcp.oauth.token.exchange.failed",
                client_id=request.client_id,
                reason="client_id_mismatch",
            )
            raise OAuthError("invalid_grant", "client_id does not match code")

        if code_record.redirect_uri != request.redirect_uri:
            record(
                "mcp.oauth.token.exchange.failed",
                client_id=request.client_id,
                reason="redirect_uri_mismatch",
            )
            raise OAuthError("invalid_grant", "redirect_uri does not match code")

        if not self._verify_pkce(
            code_verifier=request.code_verifier,
            expected_challenge=code_record.code_challenge,
            method=code_record.code_challenge_method,
        ):
            record(
                "mcp.oauth.token.exchange.failed",
                client_id=request.client_id,
                reason="pkce_failed",
            )
            raise OAuthError("invalid_grant", "PKCE verification failed")

        # All validations passed — now consume the code
        self._store.mark_code_used(request.code)

        access_token = secrets.token_urlsafe(48)
        self._store.save_access_token(
            AccessTokenRecord(
                access_token=access_token,
                client_id=request.client_id,
                scope=code_record.scope,
                expires_at=time.time() + _ACCESS_TOKEN_TTL_SECONDS,
            )
        )
        record(
            "mcp.oauth.token.issued",
            client_id=request.client_id,
            scope=code_record.scope,
            expires_in=_ACCESS_TOKEN_TTL_SECONDS,
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
            "scope": code_record.scope,
        }

    def validate_access_token(
        self,
        access_token: str,
    ) -> AccessTokenRecord | None:
        """Return token metadata for auth middleware or None when invalid/expired."""
        return self._store.fetch_access_token(access_token)

    @staticmethod
    def _verify_pkce(
        *,
        code_verifier: str,
        expected_challenge: str,
        method: str,
    ) -> bool:
        match method:
            case "S256":
                digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
                computed = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
                return computed == expected_challenge
            case _:
                return False
