"""OAuth 2.1 data models for authorization codes, access tokens, and clients.

Pure dataclasses with no business logic — consumed by the store and service
layers.  Separated from config models to keep config-loading concerns out
of the runtime token/code lifecycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True, kw_only=True)
class AuthorizationCodeRecord:
    """Single-use authorization code persisted until exchanged or expired.

    Carries PKCE challenge material and redirect binding so token exchange can
    validate caller proof before minting an access token.
    """

    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    expires_at: float
    issued_at: float = field(default_factory=time.time)
    used: bool = False


@dataclass(slots=True, kw_only=True)
class AccessTokenRecord:
    """Issued bearer token metadata used by middleware request admission checks."""

    access_token: str
    client_id: str
    scope: str
    expires_at: float
    issued_at: float = field(default_factory=time.time)


@dataclass(slots=True, kw_only=True)
class RegisteredClient:
    """Trusted OAuth client registration loaded from operator-managed config."""

    client_id: str
    redirect_uris: list[str]
    client_secret: str | None = None
