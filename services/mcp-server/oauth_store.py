"""OAuth record store with optional SQLite persistence.

Request handlers run on one asyncio event-loop thread, so dictionary get/set/pop
operations are atomic enough without explicit lock primitives.  Expired records
are lazily cleaned on fetch and eagerly purged by ``purge_expired()``.

When ``db_path`` is provided, access tokens and client registrations are
persisted to SQLite so they survive container restarts.  Authorization codes
remain in-memory only (short-lived, single-use).  The in-memory dicts are
the primary lookup path; SQLite is the write-through persistence layer.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from mcp_events import record
from oauth_models import AccessTokenRecord, AuthorizationCodeRecord, RegisteredClient

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS access_tokens (
    access_token TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    scope        TEXT NOT NULL,
    expires_at   REAL NOT NULL,
    issued_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clients (
    client_id     TEXT PRIMARY KEY,
    client_secret TEXT,
    redirect_uris TEXT NOT NULL
);
"""


class OAuthStore:
    """Manage authorization codes, access tokens, and client registrations.

    All mutations are single-threaded dict operations — no locking required
    under the ASGI event-loop concurrency model.  When ``db_path`` is set,
    tokens and clients are persisted to SQLite (write-through, warm on init).
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        self._authorization_codes: dict[str, AuthorizationCodeRecord] = {}
        self._access_tokens: dict[str, AccessTokenRecord] = {}
        self._clients: dict[str, RegisteredClient] = {}
        self._db: sqlite3.Connection | None = None
        if db_path is not None:
            self._init_db(db_path)
            self._warm_from_db()

    def _init_db(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, isolation_level="DEFERRED")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA_SQL)
        logger.info("OAuth store: SQLite persistence at %s", db_path)

    def _warm_from_db(self) -> None:
        if self._db is None:
            return
        now = time.time()
        # Purge expired tokens before loading
        self._db.execute("DELETE FROM access_tokens WHERE expires_at <= ?", (now,))
        self._db.commit()

        cur = self._db.execute(
            "SELECT access_token, client_id, scope, expires_at, issued_at "
            "FROM access_tokens"
        )
        token_count = 0
        for row in cur:
            rec = AccessTokenRecord(
                access_token=row[0],
                client_id=row[1],
                scope=row[2],
                expires_at=row[3],
                issued_at=row[4],
            )
            self._access_tokens[rec.access_token] = rec
            token_count += 1

        cur = self._db.execute(
            "SELECT client_id, client_secret, redirect_uris FROM clients"
        )
        client_count = 0
        for row in cur:
            uris: list[str] = json.loads(row[2])
            client = RegisteredClient(
                client_id=row[0],
                client_secret=row[1],
                redirect_uris=uris,
            )
            self._clients[client.client_id] = client
            client_count += 1

        if token_count or client_count:
            logger.info(
                "OAuth store: warmed %d tokens, %d clients from SQLite",
                token_count,
                client_count,
            )

    def save_client(self, client: RegisteredClient) -> None:
        self._clients[client.client_id] = client
        if self._db is not None:
            self._db.execute(
                "INSERT OR REPLACE INTO clients "
                "(client_id, client_secret, redirect_uris) VALUES (?, ?, ?)",
                (
                    client.client_id,
                    client.client_secret,
                    json.dumps(client.redirect_uris),
                ),
            )
            self._db.commit()

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
        if self._db is not None:
            self._db.execute(
                "INSERT OR REPLACE INTO access_tokens "
                "(access_token, client_id, scope, expires_at, issued_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    token_record.access_token,
                    token_record.client_id,
                    token_record.scope,
                    token_record.expires_at,
                    token_record.issued_at,
                ),
            )
            self._db.commit()

    def fetch_access_token(self, access_token: str) -> AccessTokenRecord | None:
        token_record = self._access_tokens.get(access_token)
        if token_record is None:
            return None
        if token_record.expires_at <= time.time():
            self._access_tokens.pop(access_token, None)
            if self._db is not None:
                self._db.execute(
                    "DELETE FROM access_tokens WHERE access_token = ?",
                    (access_token,),
                )
                self._db.commit()
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

        if self._db is not None and expired_tokens:
            self._db.execute("DELETE FROM access_tokens WHERE expires_at <= ?", (now,))
            self._db.commit()

    def close(self) -> None:
        """Close the SQLite connection when persistence is enabled."""
        if self._db is None:
            return
        try:
            self._db.close()
        finally:
            self._db = None
