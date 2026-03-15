"""Starlette route handlers for OAuth 2.1 well-known metadata, authorization, and token exchange.

Provides ``build_oauth_routes()`` which returns a list of ``Route`` objects
to mount on the Starlette app before middleware wrapping.  The consent page
is a minimal server-rendered HTML form — no JS framework needed.
"""

from __future__ import annotations

import html

from oauth_service import AuthorizationRequest, OAuthError, OAuthService
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route


def build_oauth_routes(oauth_service: OAuthService) -> list[Route]:
    """Build Starlette Route objects for OAuth endpoints."""

    async def resource_metadata(_request: Request) -> Response:
        return JSONResponse(
            oauth_service.build_protected_resource_metadata(),
            headers={"cache-control": "public, max-age=3600"},
        )

    async def authorization_metadata(_request: Request) -> Response:
        return JSONResponse(
            oauth_service.build_authorization_server_metadata(),
            headers={"cache-control": "public, max-age=3600"},
        )

    async def authorize_get(request: Request) -> Response:
        try:
            auth_req = oauth_service.validate_authorization_request(
                response_type=request.query_params.get("response_type", ""),
                client_id=request.query_params.get("client_id", ""),
                redirect_uri=request.query_params.get("redirect_uri", ""),
                scope=request.query_params.get("scope", ""),
                state=request.query_params.get("state"),
                code_challenge=request.query_params.get("code_challenge", ""),
                code_challenge_method=request.query_params.get(
                    "code_challenge_method", ""
                ),
            )
        except OAuthError as exc:
            return JSONResponse(
                {"error": exc.error, "error_description": exc.description},
                status_code=400,
            )
        return _render_consent_page(auth_req)

    async def authorize_post(request: Request) -> Response:
        form = await request.form()
        try:
            auth_req = oauth_service.validate_authorization_request(
                response_type=str(form.get("response_type", "")),
                client_id=str(form.get("client_id", "")),
                redirect_uri=str(form.get("redirect_uri", "")),
                scope=str(form.get("scope", "")),
                state=str(form.get("state", "")) or None,
                code_challenge=str(form.get("code_challenge", "")),
                code_challenge_method=str(form.get("code_challenge_method", "")),
            )
            code = oauth_service.issue_authorization_code(auth_req)
            redirect_url = oauth_service.build_redirect_uri(
                redirect_uri=auth_req.redirect_uri,
                code=code,
                state=auth_req.state,
            )
            return RedirectResponse(redirect_url, status_code=302)
        except OAuthError as exc:
            return JSONResponse(
                {"error": exc.error, "error_description": exc.description},
                status_code=400,
            )

    async def token_exchange(request: Request) -> Response:
        form = await request.form()
        try:
            exchange_req = oauth_service.validate_token_exchange(
                grant_type=str(form.get("grant_type", "")),
                code=str(form.get("code", "")),
                redirect_uri=str(form.get("redirect_uri", "")),
                client_id=str(form.get("client_id", "")),
                code_verifier=str(form.get("code_verifier", "")),
                client_secret=str(form.get("client_secret", "")) or None,
            )
            token_resp = oauth_service.exchange_authorization_code(exchange_req)
            return JSONResponse(token_resp)
        except OAuthError as exc:
            return JSONResponse(
                {"error": exc.error, "error_description": exc.description},
                status_code=400,
            )

    return [
        Route(
            "/.well-known/oauth-protected-resource", resource_metadata, methods=["GET"]
        ),
        Route(
            "/.well-known/oauth-authorization-server",
            authorization_metadata,
            methods=["GET"],
        ),
        Route("/oauth/authorize", authorize_get, methods=["GET"]),
        Route("/oauth/authorize", authorize_post, methods=["POST"]),
        Route("/oauth/token", token_exchange, methods=["POST"]),
    ]


def _render_consent_page(auth_req: AuthorizationRequest) -> HTMLResponse:
    """Render a minimal consent approval page for the OAuth authorization flow."""
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Authorize MCP Connector</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 520px; margin: 4rem auto; padding: 0 1rem; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 1.5rem; }}
    code {{ background: #f4f4f4; padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.9em; }}
    button {{ padding: 0.7rem 1.5rem; border-radius: 8px; border: 0; background: #111; color: #fff; cursor: pointer; font-size: 1rem; }}
    button:hover {{ background: #333; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>Authorize MCP Connector</h2>
    <p>Client <code>{html.escape(auth_req.client_id)}</code> is requesting access.</p>
    <p>Scope: <code>{html.escape(auth_req.scope)}</code></p>
    <form method="post" action="/oauth/authorize">
      <input type="hidden" name="response_type" value="{html.escape(auth_req.response_type)}">
      <input type="hidden" name="client_id" value="{html.escape(auth_req.client_id)}">
      <input type="hidden" name="redirect_uri" value="{html.escape(auth_req.redirect_uri)}">
      <input type="hidden" name="scope" value="{html.escape(auth_req.scope)}">
      <input type="hidden" name="state" value="{html.escape(auth_req.state or "")}">
      <input type="hidden" name="code_challenge" value="{html.escape(auth_req.code_challenge)}">
      <input type="hidden" name="code_challenge_method" value="{html.escape(auth_req.code_challenge_method)}">
      <button type="submit">Approve</button>
    </form>
  </div>
</body>
</html>""")
