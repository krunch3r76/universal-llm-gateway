"""Refresh-token grant: rotation, family revocation on reuse, and token pair minting."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from mcp_events import record
from oauth_models import AccessTokenRecord, RefreshTokenRecord, RegisteredClient
from oauth_store import OAuthStore

_ACCESS_TOKEN_TTL_SECONDS = 604_800  # 7 days — must match oauth_service
_REFRESH_TOKEN_TTL_SECONDS = 90 * 86_400


@dataclass(slots=True, kw_only=True)
class RefreshExchangeRequest:
    """Validated refresh-token grant ready for rotation."""

    grant_type: str
    refresh_token: str
    client_id: str
    client_secret: str | None


class OAuthRefreshError(ValueError):
    """RFC 6749 error for refresh grant (mirrors oauth_service.OAuthError shape)."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


def new_family_id() -> str:
    return secrets.token_urlsafe(16)


def mint_access_and_refresh_tokens(
    store: OAuthStore,
    *,
    client_id: str,
    scope: str,
    family_id: str,
    now: float | None = None,
) -> tuple[str, str]:
    """Persist a new access/refresh pair in the given family; return token strings."""
    issued = now if now is not None else time.time()
    access_token = secrets.token_urlsafe(48)
    refresh_token = secrets.token_urlsafe(48)
    store.save_access_token(
        AccessTokenRecord(
            access_token=access_token,
            client_id=client_id,
            scope=scope,
            expires_at=issued + _ACCESS_TOKEN_TTL_SECONDS,
            issued_at=issued,
            family_id=family_id,
        )
    )
    store.save_refresh_token(
        RefreshTokenRecord(
            refresh_token=refresh_token,
            client_id=client_id,
            scope=scope,
            expires_at=issued + _REFRESH_TOKEN_TTL_SECONDS,
            family_id=family_id,
            issued_at=issued,
        )
    )
    return access_token, refresh_token


def token_response(
    *,
    access_token: str,
    refresh_token: str,
    scope: str,
) -> dict[str, object]:
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
        "scope": scope,
        "refresh_token": refresh_token,
    }


def validate_refresh_exchange(
    client: RegisteredClient | None,
    *,
    grant_type: str,
    refresh_token: str,
    client_id: str,
    client_secret: str | None,
) -> RefreshExchangeRequest:
    if grant_type != "refresh_token":
        raise OAuthRefreshError(
            "unsupported_grant_type", "grant_type must be refresh_token"
        )
    if not refresh_token.strip():
        raise OAuthRefreshError("invalid_request", "refresh_token is required")
    if client is None:
        raise OAuthRefreshError("invalid_client", "Unknown client_id")
    match client.client_secret:
        case None:
            pass
        case expected if client_secret == expected:
            pass
        case _:
            raise OAuthRefreshError("invalid_client", "client_secret mismatch")
    return RefreshExchangeRequest(
        grant_type=grant_type,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )


def exchange_refresh_token(
    store: OAuthStore,
    request: RefreshExchangeRequest,
) -> dict[str, object]:
    store.purge_expired()
    token_record = store.fetch_refresh_token(request.refresh_token)
    if token_record is None:
        raise OAuthRefreshError("invalid_grant", "Refresh token is invalid or expired")

    if token_record.used:
        store.revoke_token_family(token_record.family_id)
        record(
            "mcp.oauth.token.refresh.failed",
            client_id=request.client_id,
            reason="refresh_token_reuse",
        )
        raise OAuthRefreshError("invalid_grant", "Refresh token has already been used")

    if token_record.client_id != request.client_id:
        raise OAuthRefreshError("invalid_grant", "client_id does not match refresh token")

    store.mark_refresh_token_used(request.refresh_token)

    access_token, refresh_token = mint_access_and_refresh_tokens(
        store,
        client_id=token_record.client_id,
        scope=token_record.scope,
        family_id=token_record.family_id,
    )
    record(
        "mcp.oauth.token.refreshed",
        client_id=token_record.client_id,
        scope=token_record.scope,
        expires_in=_ACCESS_TOKEN_TTL_SECONDS,
    )
    return token_response(
        access_token=access_token,
        refresh_token=refresh_token,
        scope=token_record.scope,
    )
