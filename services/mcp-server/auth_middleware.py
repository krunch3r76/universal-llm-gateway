"""Auth admission middleware — static bearer and OAuth token validation.

Owns public-path bypass, ``/clip`` handling, static-token admission, OAuth
bearer validation, and ``WWW-Authenticate`` response construction.  Does
**not** emit ``mcp.request.*`` lifecycle events — it only annotates
authenticated requests with ``scope["auth_mode"]`` so downstream middleware
can include the admission path in telemetry.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from mcp_events import record
from oauth_service import OAuthService
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from tools.clip import normalize_clip_content
from workbench_relay import (
    _CORS_HEADERS as RELAY_CORS_HEADERS,
    handle_preflight,
    handle_relay,
)

logger = logging.getLogger(__name__)

PUBLIC_PATHS = frozenset(
    {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/oauth/authorize",
        "/oauth/token",
    }
)


class AuthMiddleware:
    """Admit HTTP requests via static bearer or OAuth without owning MCP telemetry.

    Public OAuth bootstrap endpoints bypass auth entirely. Authenticated requests
    carry ``auth_mode`` on the ASGI scope so downstream middleware can emit
    ``mcp.request.*`` events with the final admission path.
    """

    _CLIPS_DIR = Path("/data/files/clips")
    _MAX_BODY_BYTES = 5 * 1024 * 1024
    _CORS_HEADERS: dict[str, str] = {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "POST, OPTIONS",
        "access-control-allow-headers": "Authorization, Content-Type",
        "access-control-max-age": "86400",
    }

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        oauth_service: OAuthService | None,
    ) -> None:
        self._app = app
        self._token = token
        self._oauth_service = oauth_service
        self._cursor_token = os.getenv("MCP_CURSOR_AUTH_TOKEN", "").strip()

    @staticmethod
    def _slugify(text: str, max_len: int = 60) -> str:
        """Convert a string to a URL-safe slug for persisted clip filenames."""
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
        slug = slug.strip("-")[:max_len].rstrip("-")
        return slug or "untitled"

    @staticmethod
    def _extract_bearer_token(authorization: str) -> str | None:
        """Return the bearer token value or None when the header is malformed."""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _build_www_authenticate_header(self) -> str | None:
        """Return OAuth resource metadata hint for 401 responses when OAuth is enabled."""
        if self._oauth_service is None:
            return None
        return (
            'Bearer realm="mcp", '
            f'resource_metadata="{self._oauth_service.resource_metadata_url}"'
        )

    def _resolve_profile(self, auth_header: str) -> str:
        """Map static bearer tokens to MCP request profiles.

        If no dedicated Cursor token is configured, all static tokens map to
        ``default``. When a Cursor token is configured and matched, requests map
        to ``cursor_safe``. All other static tokens map to ``default``.
        """
        if not self._cursor_token:
            return "default"
        if auth_header == f"Bearer {self._cursor_token}":
            return "cursor_safe"
        return "default"

    def _is_static_token_authorized(self, auth_header: str) -> bool:
        """Return True when auth header matches configured static token(s)."""
        if auth_header == f"Bearer {self._token}":
            return True
        if self._cursor_token and auth_header == f"Bearer {self._cursor_token}":
            return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        if path == "/health":
            response = JSONResponse({"status": "ok"})
            await response(scope, receive, send)
            return

        if path in PUBLIC_PATHS:
            await self._app(scope, receive, send)
            return

        if path == "/clip":
            auth = request.headers.get("authorization", "")
            if request.method == "OPTIONS":
                response = JSONResponse({"status": "ok"}, headers=self._CORS_HEADERS)
                await response(scope, receive, send)
                return
            if auth != f"Bearer {self._token}":
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers=self._CORS_HEADERS,
                )
                await response(scope, receive, send)
                return
            if request.method == "POST":
                response = await self._handle_clip(request)
                await response(scope, receive, send)
                return
            response = JSONResponse(
                {"error": "Method not allowed"},
                status_code=405,
                headers=self._CORS_HEADERS,
            )
            await response(scope, receive, send)
            return

        if path == "/workbench/relay":
            if request.method == "OPTIONS":
                response = handle_preflight()
                await response(scope, receive, send)
                return
            auth = request.headers.get("authorization", "")
            if not self._is_static_token_authorized(auth):
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers=RELAY_CORS_HEADERS,
                )
                await response(scope, receive, send)
                return
            if request.method == "POST":
                response = await handle_relay(request)
                await response(scope, receive, send)
                return
            response = JSONResponse(
                {"error": "Method not allowed"},
                status_code=405,
                headers=RELAY_CORS_HEADERS,
            )
            await response(scope, receive, send)
            return

        auth_header = request.headers.get("authorization", "")
        if self._is_static_token_authorized(auth_header):
            scope["auth_mode"] = "static"
            scope["mcp_profile"] = self._resolve_profile(auth_header)
            record(
                "mcp.profile.bound",
                profile=scope["mcp_profile"],
                auth_mode="static",
            )
            await self._app(scope, receive, send)
            return

        token = self._extract_bearer_token(auth_header)
        if token is not None and self._oauth_service is not None:
            token_record = self._oauth_service.validate_access_token(token)
            if token_record is not None:
                record("mcp.oauth.token.accepted", client_id=token_record.client_id)
                scope["auth_mode"] = "oauth"
                scope["oauth_client_id"] = token_record.client_id
                scope["mcp_profile"] = "default"
                record(
                    "mcp.profile.bound",
                    profile="default",
                    auth_mode="oauth",
                )
                await self._app(scope, receive, send)
                return
            record("mcp.oauth.token.rejected", reason="unknown_or_expired")

        if token is not None:
            record(
                "mcp.profile.rejected",
                reason="unauthorized_token",
            )

        headers: dict[str, str] = {}
        if www_authenticate := self._build_www_authenticate_header():
            headers["WWW-Authenticate"] = www_authenticate
        response = JSONResponse(
            {"error": "Unauthorized"}, status_code=401, headers=headers
        )
        await response(scope, receive, send)

    async def _handle_clip(self, request: Request) -> JSONResponse:
        """Process a bookmarklet clip upload after static-token authentication."""
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > self._MAX_BODY_BYTES:
                return JSONResponse(
                    {"error": "Payload too large (5MB limit)"},
                    status_code=413,
                    headers=self._CORS_HEADERS,
                )

        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return JSONResponse(
                {"error": "Invalid JSON"},
                status_code=400,
                headers=self._CORS_HEADERS,
            )

        url = data.get("url", "").strip()
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        selected = bool(data.get("selected", False))

        if not content:
            return JSONResponse(
                {"error": "Missing required field: content"},
                status_code=400,
                headers=self._CORS_HEADERS,
            )

        content, extracted = normalize_clip_content(content)
        if not title:
            title = "Untitled Clip"

        ts = int(time.time())
        slug = self._slugify(title)
        filename = f"{slug}-{ts}.md"
        self._CLIPS_DIR.mkdir(parents=True, exist_ok=True)

        title_sanitized = title.replace("\r", "").replace("\n", " ")
        url_sanitized = url.replace("\r", "").replace("\n", " ")
        safe_title = title_sanitized.replace("\\", "\\\\").replace('"', '\\"')
        safe_url = url_sanitized.replace("\\", "\\\\").replace('"', '\\"')
        frontmatter = (
            f"---\n"
            f'url: "{safe_url}"\n'
            f'title: "{safe_title}"\n'
            f"clipped_at: {ts}\n"
            f"selected: {str(selected).lower()}\n"
            f"extracted: {str(extracted).lower()}\n"
            f"chars: {len(content)}\n"
            f"---\n\n"
        )

        for attempt in range(5):
            candidate = self._CLIPS_DIR / (
                f"{slug}-{ts + attempt}.md" if attempt else filename
            )
            try:
                with candidate.open("x", encoding="utf-8") as clip_file:
                    clip_file.write(frontmatter + content)
                filename = candidate.name
                break
            except FileExistsError:
                continue
        else:
            return JSONResponse(
                {"error": f"Unable to allocate unique clip filename for slug '{slug}'"},
                status_code=409,
                headers=self._CORS_HEADERS,
            )

        logger.info(
            "clip: saved %s (%d chars, selected=%s)", filename, len(content), selected
        )
        return JSONResponse(
            {"status": "clipped", "clip_id": filename},
            headers=self._CORS_HEADERS,
        )
