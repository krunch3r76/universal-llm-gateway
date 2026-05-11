"""Auth middleware token parsing tests."""

from __future__ import annotations

import sys
import types
from typing import Any

trafilatura = types.ModuleType("trafilatura")
trafilatura.extract = lambda *_args, **_kwargs: None
sys.modules.setdefault("trafilatura", trafilatura)


def _auth_middleware_cls() -> Any:
    from auth_middleware import AuthMiddleware

    return AuthMiddleware


def test_extract_authorization_token_accepts_bearer() -> None:
    auth_middleware = _auth_middleware_cls()
    assert auth_middleware._extract_authorization_token("Bearer token-123") == "token-123"


def test_extract_authorization_token_accepts_raw_token() -> None:
    auth_middleware = _auth_middleware_cls()
    assert auth_middleware._extract_authorization_token("token-123") == "token-123"


def test_extract_authorization_token_rejects_unknown_scheme() -> None:
    auth_middleware = _auth_middleware_cls()
    assert auth_middleware._extract_authorization_token("Basic token-123") is None


def test_path_suffixed_oauth_metadata_is_public() -> None:
    from auth_middleware import _is_public_path

    assert _is_public_path("/.well-known/oauth-protected-resource/mcp")
    assert _is_public_path("/.well-known/oauth-authorization-server/mcp")
