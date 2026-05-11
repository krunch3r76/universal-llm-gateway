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
from typing import TYPE_CHECKING

from artifact_proxy import (
    handle_artifact_preflight,
    handle_artifact_proxy,
)
from cortex_proxy import (
    _CORS_HEADERS as CORTEX_CORS_HEADERS,
)
from cortex_proxy import (
    handle_cortex_preflight,
    handle_cortex_proxy,
)
from llm_proxy import (
    _CORS_HEADERS as LLM_CORS_HEADERS,
)
from llm_proxy import (
    handle_llm_preflight,
    handle_llm_proxy,
)
from mcp_events import record
from starlette.requests import Request
from starlette.responses import JSONResponse
from tools.clip import normalize_clip_content
from workbench_relay import (
    _CORS_HEADERS as RELAY_CORS_HEADERS,
)
from workbench_relay import (
    handle_preflight,
    handle_relay,
)

if TYPE_CHECKING:
    from oauth_service import OAuthService
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

PUBLIC_PATHS = frozenset(
    {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
        "/oauth/authorize",
        "/oauth/register",
        "/oauth/token",
    }
)
PUBLIC_PATH_PREFIXES = (
    "/.well-known/oauth-protected-resource/",
    "/.well-known/oauth-authorization-server/",
    "/.well-known/openid-configuration/",
)


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES)


class AuthMiddleware:
    """Admit HTTP requests via static bearer or OAuth without owning MCP telemetry.

    Public OAuth bootstrap endpoints bypass auth entirely. Authenticated requests
    carry ``auth_mode`` on the ASGI scope so downstream middleware can emit
    ``mcp.request.*`` events with the final admission path.
    """

    _CLIPS_DIR = Path("/data/files/clips")
    _MAX_BODY_BYTES = 5 * 1024 * 1024
    _CLIP_CORS_HEADERS: dict[str, str] = {
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
    def _extract_authorization_token(authorization: str) -> str | None:
        """Return token from ``Authorization``.

        Accept both standard ``Bearer <token>`` and raw token values. xAI's
        Remote MCP docs say the configured value is placed in the Authorization
        header; the API path currently normalizes bearer values, but the Web
        connector's custom-token UI is ambiguous. Raw-token acceptance keeps
        auth mandatory while making that connector shape work.
        """
        authorization = authorization.strip()
        if not authorization:
            return None
        scheme, _, token = authorization.partition(" ")
        if not token:
            return scheme.strip()
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _build_www_authenticate_header(self) -> str | None:
        """Return the 'WWW-Authenticate' header value for 401 responses, including OAuth resource metadata hint if OAuth is enabled. The format is 'Bearer realm="mcp", resource_metadata="<URL>"'."""
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
        token = self._extract_authorization_token(auth_header)
        if self._cursor_token and token == self._cursor_token:
            return "cursor_safe"
        return "default"

    def _is_static_token_authorized(self, auth_header: str) -> bool:
        """Return True when auth header matches configured static token(s)."""
        token = self._extract_authorization_token(auth_header)
        return token == self._token or (
            self._cursor_token and token == self._cursor_token
        )

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

        # Artifact proxy — origin-validated, no bearer token required
        if path.startswith("/artifact/cortex"):
            if request.method == "OPTIONS":
                response = handle_artifact_preflight(request)
                await response(scope, receive, send)
                return
            response = await handle_artifact_proxy(request)
            await response(scope, receive, send)
            return

        if _is_public_path(path):
            await self._app(scope, receive, send)
            return

        if path == "/clip":
            auth = request.headers.get("authorization", "")
            if request.method == "OPTIONS":
                response = JSONResponse(
                    {"status": "ok"}, headers=self._CLIP_CORS_HEADERS
                )
                await response(scope, receive, send)
                return
            if not self._is_static_token_authorized(auth):
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers=self._CLIP_CORS_HEADERS,
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
                headers=self._CLIP_CORS_HEADERS,
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

        if path.startswith("/cortex-api"):
            if request.method == "OPTIONS":
                response = handle_cortex_preflight()
                await response(scope, receive, send)
                return
            auth = request.headers.get("authorization", "")
            if not self._is_static_token_authorized(auth):
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers=CORTEX_CORS_HEADERS,
                )
                await response(scope, receive, send)
                return
            response = await handle_cortex_proxy(request)
            await response(scope, receive, send)
            return

        if path.startswith("/llm/"):
            if request.method == "OPTIONS":
                response = handle_llm_preflight()
                await response(scope, receive, send)
                return
            auth = request.headers.get("authorization", "")
            if not self._is_static_token_authorized(auth):
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers=LLM_CORS_HEADERS,
                )
                await response(scope, receive, send)
                return
            if request.method == "POST" and path == "/llm/v1/messages":
                response = await handle_llm_proxy(request)
                await response(scope, receive, send)
                return
            response = JSONResponse(
                {"error": "Not found"},
                status_code=404,
                headers=LLM_CORS_HEADERS,
            )
            await response(scope, receive, send)
            return

        auth_header = request.headers.get("authorization", "")
        client_ip = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else 0
        user_agent = request.headers.get("user-agent", "")

        if self._is_static_token_authorized(auth_header):
            scope["auth_mode"] = "static"
            scope["mcp_profile"] = self._resolve_profile(auth_header)
            record(
                "mcp.auth.admitted",
                client_ip=client_ip,
                client_port=client_port,
                auth_mode="static",
                path=path,
                user_agent=user_agent,
            )
            record(
                "mcp.profile.bound",
                profile=scope["mcp_profile"],
                auth_mode="static",
            )
            await self._app(scope, receive, send)
            return

        token = self._extract_authorization_token(auth_header)
        if token is not None and self._oauth_service is not None:
            token_record = self._oauth_service.validate_access_token(token)
            if token_record is not None:
                record(
                    "mcp.auth.admitted",
                    client_ip=client_ip,
                    client_port=client_port,
                    auth_mode="oauth",
                    oauth_client_id=token_record.client_id,
                    path=path,
                    user_agent=user_agent,
                )
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

        record(
            "mcp.request.unauthorized",
            client_ip=client_ip,
            client_port=client_port,
            path=path,
            user_agent=user_agent,
            reason="no_valid_token",
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
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > self._MAX_BODY_BYTES:
                logger.warning("clip: rejected oversized payload (%d bytes)", len(body))
                record(
                    "mcp.clip.upload_failed", reason="payload_too_large", size=len(body)
                )
                return JSONResponse(
                    {"error": "Payload too large (5MB limit)"},
                    status_code=413,
                    headers=self._CLIP_CORS_HEADERS,
                )

        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.warning("clip: rejected invalid JSON payload")
            return JSONResponse(
                {"error": "Invalid JSON"},
                status_code=400,
                headers=self._CLIP_CORS_HEADERS,
            )

        url = data.get("url", "").strip()
        title = data.get("title", "").strip()
        content = data.get("content", "").strip()
        selected = bool(data.get("selected", False))

        if not content:
            logger.warning("clip: rejected empty content payload")
            return JSONResponse(
                {"error": "Missing required field: content"},
                status_code=400,
                headers=self._CLIP_CORS_HEADERS,
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
        frontmatter = f"""
---
url: "{safe_url}"
title: "{safe_title}"
clipped_at: {ts}
selected: {str(selected).lower()}
extracted: {str(extracted).lower()}
chars: {len(content)}
---

"""

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
                logger.debug("clip: filename '%s' already exists, retrying", candidate)
                continue
            except OSError as exc:
                logger.error("clip: failed writing %s: %s", candidate, exc)
                return JSONResponse(
                    {"error": "Failed to save clip"},
                    status_code=500,
                    headers=self._CLIP_CORS_HEADERS,
                )
        else:
            logger.error("clip: unable to allocate unique filename for slug '%s'", slug)
            return JSONResponse(
                {"error": f"Unable to allocate unique clip filename for slug '{slug}'"},
                status_code=409,
                headers=self._CLIP_CORS_HEADERS,
            )

        logger.info(
            "clip: saved %s (%d chars, selected=%s)", filename, len(content), selected
        )
        return JSONResponse(
            {"status": "clipped", "clip_id": filename},
            headers=self._CLIP_CORS_HEADERS,
        )
