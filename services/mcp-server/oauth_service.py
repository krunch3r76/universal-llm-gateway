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
from fnmatch import fnmatchcase
from urllib.parse import urlencode, urlparse

from mcp_events import record
from oauth_config import OAuthServerConfig
from oauth_models import AccessTokenRecord, AuthorizationCodeRecord, RegisteredClient
from oauth_store import OAuthStore

logger = logging.getLogger(__name__)

_AUTHORIZATION_CODE_TTL_SECONDS = 300
_ACCESS_TOKEN_TTL_SECONDS = 604_800  # 7 days — persisted tokens need long TTL


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

    @property
    def registration_endpoint(self) -> str:
        return f"{self._config.issuer}{self._config.registration_path}"

    def build_protected_resource_metadata(
        self, *, resource_path: str | None = None
    ) -> dict[str, object]:
        """RFC 9728 protected-resource metadata.

        When ``resource_path`` is set (e.g. ``mcp/life`` from
        ``/.well-known/oauth-protected-resource/{resource_path}``), advertise
        that exact resource URL so dual-endpoint OAuth clients bind correctly.
        """
        if resource_path and resource_path.strip():
            resource = f"{self._config.issuer}/{resource_path.strip().lstrip('/')}"
        else:
            resource = self._config.resource_server_url
        return {
            "resource": resource,
            "authorization_servers": [self._config.issuer],
            "bearer_methods_supported": ["header"],
        }

    def resource_metadata_url_for(self, mcp_path: str) -> str:
        """Return path-scoped resource-metadata URL for a MCP mount (``/mcp/life``)."""
        suffix = mcp_path.strip().lstrip("/")
        base = f"{self._config.issuer}{self._config.resource_metadata_path}"
        return f"{base}/{suffix}" if suffix else base

    def build_authorization_server_metadata(self) -> dict[str, object]:
        return {
            "issuer": self._config.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "registration_endpoint": self.registration_endpoint,
            "scopes_supported": self._config.supported_scopes or ["mcp"],
        }

    def register_dynamic_client(self, payload: dict[str, object]) -> dict[str, object]:
        """Register an OAuth client for MCP connector onboarding (RFC 7591).

        Accepts public PKCE clients (``token_endpoint_auth_method=none``) and
        confidential clients (``client_secret_post``). Claude.ai picks the latter
        when AS metadata advertises both — reject-only-``none`` breaks Connect.
        Authorization-code + PKCE only; redirect hosts constrained by config.
        """
        redirect_uris = self._parse_redirect_uris(payload.get("redirect_uris"))
        token_auth_method = str(
            payload.get("token_endpoint_auth_method", "none") or "none"
        )
        if token_auth_method not in {"none", "client_secret_post"}:
            raise OAuthError(
                "invalid_client_metadata",
                "token_endpoint_auth_method must be none or client_secret_post",
            )

        grant_types = payload.get("grant_types", ["authorization_code"])
        if not _valid_dynamic_grant_types(grant_types):
            raise OAuthError(
                "invalid_client_metadata",
                "authorization_code grant_type is required",
            )

        response_types = payload.get("response_types", ["code"])
        if not _list_subset(response_types, {"code"}):
            raise OAuthError(
                "invalid_client_metadata",
                "only code response_type is supported",
            )

        client_id = f"dyn-{secrets.token_urlsafe(24)}"
        client_secret: str | None = None
        if token_auth_method == "client_secret_post":
            client_secret = secrets.token_urlsafe(32)
        self._store.save_client(
            RegisteredClient(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=redirect_uris,
            )
        )
        record(
            "mcp.oauth.dynamic_client.registered",
            client_id=client_id,
            token_endpoint_auth_method=token_auth_method,
            redirect_hosts=sorted(
                {urlparse(uri).hostname or "" for uri in redirect_uris}
            ),
        )
        registration: dict[str, object] = {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": token_auth_method,
        }
        if client_secret is not None:
            registration["client_secret"] = client_secret
        return registration

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

    def _parse_redirect_uris(self, raw_redirect_uris: object) -> list[str]:
        if not isinstance(raw_redirect_uris, list):
            raise OAuthError(
                "invalid_client_metadata",
                "redirect_uris must be a non-empty list",
            )
        redirect_uris = [
            value.strip()
            for value in raw_redirect_uris
            if isinstance(value, str) and value.strip()
        ]
        if not redirect_uris:
            raise OAuthError(
                "invalid_client_metadata",
                "redirect_uris must be a non-empty list",
            )
        for redirect_uri in redirect_uris:
            self._validate_dynamic_redirect_uri(redirect_uri)
        return redirect_uris

    def _validate_dynamic_redirect_uri(self, redirect_uri: str) -> None:
        parsed = urlparse(redirect_uri)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise OAuthError(
                "invalid_redirect_uri",
                "dynamic redirect_uri must be an https URL",
            )

        allowed_hosts = self._config.dynamic_client_redirect_hosts or []
        if not any(_host_allowed(host, pattern) for pattern in allowed_hosts):
            raise OAuthError(
                "invalid_redirect_uri",
                "dynamic redirect_uri host is not allowed",
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


def _list_subset(value: object, allowed: set[str]) -> bool:
    if not isinstance(value, list):
        return False
    values = {item for item in value if isinstance(item, str)}
    return bool(values) and values.issubset(allowed)


def _valid_dynamic_grant_types(value: object) -> bool:
    """Return True for DCR grant metadata compatible with this server.

    Some MCP clients register both authorization_code and refresh_token even
    when the initial connection only needs the authorization-code flow.  We do
    not issue refresh tokens, but accepting this metadata lets the client reach
    the consent + token exchange path and rely on the returned grant_types.
    """
    if not isinstance(value, list):
        return False
    values = {item for item in value if isinstance(item, str)}
    return "authorization_code" in values and values.issubset(
        {"authorization_code", "refresh_token"}
    )


def _host_allowed(host: str, pattern: str) -> bool:
    pattern = pattern.lower()
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != pattern[2:]
    return fnmatchcase(host, pattern)
