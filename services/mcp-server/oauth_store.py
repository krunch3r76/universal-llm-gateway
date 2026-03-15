"""Lock-free in-memory OAuth record store for a single-process ASGI server.

Request handlers run on one asyncio event-loop thread, so dictionary get/set/pop
operations are atomic enough without explicit lock primitives.  Expired records
are lazily cleaned on fetch and eagerly purged by ``purge_expired()``.
"""

from __future__ import annotations

import time

from mcp_events import record
from oauth_models import AccessTokenRecord, AuthorizationCodeRecord, RegisteredClient


class OAuthStore:
    """Manage authorization codes, access tokens, and client registrations.

    All mutations are single-threaded dict operations — no locking required
    under the ASGI event-loop concurrency model.
    """

    def __init__(self) -> None:
        self._authorization_codes: dict[str, AuthorizationCodeRecord] = {}
        self._access_tokens: dict[str, AccessTokenRecord] = {}
        self._clients: dict[str, RegisteredClient] = {}

    def save_client(self, client: RegisteredClient) -> None:
        self._clients[client.client_id] = client

    def fetch_client(self, client_id: str) -> RegisteredClient | None:
        return self._clients.get(client_id)

    def save_authorization_code(self, auth_record: AuthorizationCodeRecord) -> None:
        self._authorization_codes[auth_record.code] = auth_record

    def fetch_authorization_code(self, code: str) -> AuthorizationCodeRecord | None:
        """Fetch code for validation. Caller must call mark_code_used to consume.

        Used codes are removed here. Expired codes are left in store so
        purge_expired() can emit mcp.oauth.code.expired and remove them.
        """
        auth_record = self._authorization_codes.get(code)
        if auth_record is None:
            return None
        if auth_record.used:
            self._authorization_codes.pop(code, None)
            return None
        if auth_record.expires_at <= time.time():
            return None
        return auth_record

    def mark_code_used(self, code: str) -> None:
        auth_record = self._authorization_codes.get(code)
        if auth_record is not None:
            auth_record.used = True

    def save_access_token(self, token_record: AccessTokenRecord) -> None:
        self._access_tokens[token_record.access_token] = token_record

    def fetch_access_token(self, access_token: str) -> AccessTokenRecord | None:
        token_record = self._access_tokens.get(access_token)
        if token_record is None:
            return None
        if token_record.expires_at <= time.time():
            self._access_tokens.pop(access_token, None)
            return None
        return token_record

    def purge_expired(self) -> None:
        now = time.time()
        expired_codes = [
            code
            for code, rec in self._authorization_codes.items()
            if rec.expires_at <= now or rec.used
        ]
        for code in expired_codes:
            rec = self._authorization_codes.pop(code, None)
            if rec is not None and not rec.used and rec.expires_at <= now:
                record("mcp.oauth.code.expired", client_id=rec.client_id)

        expired_tokens = [
            tok for tok, rec in self._access_tokens.items() if rec.expires_at <= now
        ]
        for tok in expired_tokens:
            self._access_tokens.pop(tok, None)
