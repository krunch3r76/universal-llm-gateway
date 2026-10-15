"""Auth middleware token parsing tests."""

from __future__ import annotations

import os
import sys
import types
from typing import Any

import pytest
import yaml
from starlette.responses import JSONResponse
from tool_access import ANTHROPIC_API_CLIENT_ID

trafilatura = types.ModuleType("trafilatura")
trafilatura.extract = lambda *_args, **_kwargs: None
sys.modules.setdefault("trafilatura", trafilatura)


def _auth_middleware_cls() -> Any:
    from auth_middleware import AuthMiddleware

    return AuthMiddleware


def _auth_middleware(**kwargs: Any) -> Any:
    return _auth_middleware_cls()(
        lambda *_args: None, token="token-123", oauth_service=None, **kwargs
    )


def test_extract_authorization_token_accepts_bearer() -> None:
    auth_middleware = _auth_middleware()
    assert (
        auth_middleware._extract_authorization_token("Bearer token-123") == "token-123"
    )


def test_extract_authorization_token_accepts_raw_token() -> None:
    auth_middleware = _auth_middleware()
    assert auth_middleware._extract_authorization_token("token-123") == "token-123"


def test_extract_authorization_token_rejects_unknown_scheme() -> None:
    auth_middleware = _auth_middleware()
    assert auth_middleware._extract_authorization_token("Basic token-123") is None


def test_path_suffixed_oauth_metadata_is_public() -> None:
    from auth_middleware import _is_public_path

    assert _is_public_path("/.well-known/oauth-protected-resource/mcp")
    assert _is_public_path("/.well-known/oauth-authorization-server/mcp")


def test_static_caller_identity_defaults_to_static() -> None:
    auth_middleware = _auth_middleware()

    assert (
        auth_middleware._resolve_static_caller_identity("Bearer token-123") == "static"
    )


# ── VORTEX_BEARER_TOKENS multi-bearer smoke tests ────────────────────────────


def test_vortex_bearer_tokens_additional_token_accepted(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "extra-token\ncursor-token")
    auth_middleware = _auth_middleware()
    assert auth_middleware._is_static_token_authorized("Bearer extra-token")
    assert auth_middleware._is_static_token_authorized("Bearer cursor-token")


def test_vortex_bearer_tokens_primary_token_still_accepted(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "extra-token")
    auth_middleware = _auth_middleware()
    # Primary MCP_AUTH_TOKEN must remain accepted alongside extra tokens.
    assert auth_middleware._is_static_token_authorized("Bearer token-123")


def test_vortex_bearer_tokens_absent_no_change() -> None:
    os.environ.pop("VORTEX_BEARER_TOKENS", None)
    auth_middleware = _auth_middleware()
    assert len(auth_middleware._bearer_tokens) == 0
    assert not auth_middleware._is_static_token_authorized("Bearer unknown-token")


def test_vortex_bearer_tokens_blank_lines_ignored(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "\n  \nextra-token\n\n")
    auth_middleware = _auth_middleware()
    assert auth_middleware._bearer_tokens == frozenset({"extra-token"})


@pytest.fixture(autouse=True)
def _pin_callback_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    cfg = tmp_path / "mcp.yaml"
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.delenv("MCP_ANTHROPIC_CALLBACK_TOKEN", raising=False)


def _write_callback_yaml(path: Any, **fields: str) -> None:
    path.write_text(yaml.safe_dump(fields), encoding="utf-8")


async def _invoke_auth(
    *,
    path: str,
    bearer: str,
    primary_token: str = "token-123",
    monkeypatch: pytest.MonkeyPatch | None = None,
    yaml_path: Any | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int]:
    if monkeypatch is not None:
        if yaml_path is not None:
            monkeypatch.setenv("MCP_CONFIG_PATH", str(yaml_path))
        for key, value in (extra_env or {}).items():
            if value == "":
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
    captured: dict[str, Any] = {}
    status_code = 0

    async def app(scope: Any, receive: Any, send: Any) -> None:
        captured["scope"] = dict(scope)

        async def _send(message: Any) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 0)

        await JSONResponse({"ok": True})(scope, receive, _send)

    middleware = _auth_middleware_cls()(
        app, token=primary_token, oauth_service=None
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {bearer}".encode())],
        "client": ("127.0.0.1", 4242),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[Any] = []

    async def send(message: Any) -> None:
        sent.append(message)
        nonlocal status_code
        if message.get("type") == "http.response.start":
            status_code = int(message.get("status") or 0)

    await middleware(scope, receive, send)
    return captured, status_code


@pytest.mark.asyncio
async def test_anthropic_callback_denied_on_clip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = tmp_path / "mcp.yaml"
    _write_callback_yaml(
        cfg,
        auth_token="admin-primary",
        anthropic_callback_token="callback-distinct",
    )
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    _, status = await _invoke_auth(
        path="/clip",
        bearer="callback-distinct",
        primary_token="admin-primary",
    )
    assert status == 401


@pytest.mark.asyncio
async def test_anthropic_callback_stamps_mcp_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = tmp_path / "mcp.yaml"
    _write_callback_yaml(
        cfg,
        auth_token="admin-primary",
        anthropic_callback_token="callback-distinct",
    )
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    captured, status = await _invoke_auth(
        path="/mcp/code",
        bearer="callback-distinct",
        primary_token="admin-primary",
    )
    assert status == 200
    assert captured["scope"]["oauth_client_id"] == ANTHROPIC_API_CLIENT_ID


@pytest.mark.asyncio
async def test_anthropic_callback_cursor_collision_stamps_api_not_cursor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = tmp_path / "mcp.yaml"
    _write_callback_yaml(
        cfg,
        auth_token="admin-primary",
        anthropic_callback_token="cursor-shared",
    )
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("MCP_CURSOR_AUTH_TOKEN", "cursor-shared")
    captured, status = await _invoke_auth(
        path="/mcp/code",
        bearer="cursor-shared",
        primary_token="admin-primary",
    )
    assert status == 200
    assert captured["scope"]["oauth_client_id"] == ANTHROPIC_API_CLIENT_ID
    assert captured["scope"]["mcp_caller_identity"] == ANTHROPIC_API_CLIENT_ID


@pytest.mark.asyncio
async def test_anthropic_callback_vortex_collision_stamps_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = tmp_path / "mcp.yaml"
    _write_callback_yaml(
        cfg,
        auth_token="admin-primary",
        anthropic_callback_token="vortex-shared",
    )
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("VORTEX_BEARER_TOKENS", "life vortex-shared")
    captured, status = await _invoke_auth(
        path="/mcp/code",
        bearer="vortex-shared",
        primary_token="admin-primary",
    )
    assert status == 200
    assert captured["scope"]["oauth_client_id"] == ANTHROPIC_API_CLIENT_ID


@pytest.mark.asyncio
async def test_anthropic_callback_primary_collision_slot_inactive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = tmp_path / "mcp.yaml"
    _write_callback_yaml(
        cfg,
        auth_token="admin-primary",
        anthropic_callback_token="admin-primary",
    )
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    middleware = _auth_middleware_cls()(
        lambda *_a: None, token="admin-primary", oauth_service=None
    )
    assert middleware._anthropic_callback_token is None
    captured, status = await _invoke_auth(
        path="/mcp/code",
        bearer="admin-primary",
        primary_token="admin-primary",
    )
    assert status == 200
    assert "oauth_client_id" not in captured["scope"]


@pytest.mark.asyncio
async def test_resolver_yaml_admin_collision_inactive_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = tmp_path / "mcp.yaml"
    _write_callback_yaml(
        cfg,
        auth_token="callback-same-as-yaml-admin",
        anthropic_callback_token="callback-same-as-yaml-admin",
    )
    monkeypatch.setenv("MCP_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("MCP_AUTH_TOKEN", "different-env-admin")
    middleware = _auth_middleware_cls()(
        lambda *_a: None, token="different-env-admin", oauth_service=None
    )
    assert middleware._anthropic_callback_token is None
    _, status = await _invoke_auth(
        path="/mcp/code",
        bearer="callback-same-as-yaml-admin",
        primary_token="different-env-admin",
    )
    assert status == 401
