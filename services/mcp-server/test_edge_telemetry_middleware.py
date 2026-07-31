"""Edge telemetry middleware tests."""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import patch

# Same shim as test_auth_middleware: avoid importing the real trafilatura
# during test collection.  edge_telemetry_middleware does not depend on it
# directly, but the module graph through starlette.requests pulls in
# adjacent modules that do.
trafilatura = types.ModuleType("trafilatura")
trafilatura.extract = lambda *_args, **_kwargs: None
sys.modules.setdefault("trafilatura", trafilatura)


def _build_scope(
    *,
    method: str = "GET",
    path: str = "/",
    raw_query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] | None = ("198.51.100.7", 41234),
) -> dict[str, Any]:
    """Construct a minimal ASGI HTTP scope for middleware tests."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": raw_query,
        "root_path": "",
        "headers": headers or [],
        "client": client,
        "server": ("0.0.0.0", 443),
    }


async def _noop_app(_scope: Any, _receive: Any, _send: Any) -> None:
    return None


async def _empty_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(_message: Any) -> None:
    return None


def _run_middleware(scope: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Run the middleware once and return ``(signal, payload)`` records captured."""
    from edge_telemetry_middleware import EdgeTelemetryMiddleware

    captured: list[tuple[str, dict[str, Any]]] = []

    def fake_record(signal: str, **payload: Any) -> None:
        captured.append((signal, payload))

    with patch("edge_telemetry_middleware.record", side_effect=fake_record):
        middleware = EdgeTelemetryMiddleware(_noop_app)
        asyncio.run(middleware(scope, _empty_receive, _noop_send))
    return captured


def test_observation_records_base_fields_for_unauthenticated_request() -> None:
    scope = _build_scope(
        method="GET",
        path="/.well-known/oauth-protected-resource",
        headers=[
            (b"host", b"mcp.k-1.me"),
            (b"user-agent", b"connector-probe/1.0"),
            (b"accept", b"application/json"),
        ],
    )

    records = _run_middleware(scope)

    assert len(records) == 1
    signal, payload = records[0]
    assert signal == "mcp.edge.request.observed"
    assert payload["method"] == "GET"
    assert payload["path"] == "/.well-known/oauth-protected-resource"
    assert payload["client_ip"] == "198.51.100.7"
    assert payload["client_port"] == 41234
    assert payload["host"] == "mcp.k-1.me"
    assert payload["user_agent"] == "connector-probe/1.0"
    assert payload["accept"] == "application/json"
    assert payload["referer"] == ""
    assert payload["has_authorization"] is False
    assert payload["query_keys"] == []


def test_observation_redacts_authorization_header_to_boolean() -> None:
    scope = _build_scope(
        method="POST",
        path="/mcp",
        headers=[
            (b"authorization", b"Bearer secret-do-not-log"),
            (b"user-agent", b"client/2"),
        ],
    )

    records = _run_middleware(scope)

    assert records, "expected one observation event"
    payload = records[0][1]
    assert payload["has_authorization"] is True
    assert "secret-do-not-log" not in str(payload)


def test_observation_records_query_keys_only_not_values() -> None:
    scope = _build_scope(
        method="GET",
        path="/oauth/authorize",
        raw_query=b"client_id=abc&redirect_uri=https%3A%2F%2Fexample.com&state=opaque",
        headers=[(b"user-agent", b"redirect-tracer/1")],
    )

    records = _run_middleware(scope)

    assert records, "expected one observation event"
    payload = records[0][1]
    keys = payload["query_keys"]
    assert isinstance(keys, list)
    assert sorted(keys) == ["client_id", "redirect_uri", "state"]
    assert "abc" not in str(payload)
    assert "example.com" not in str(payload)
    assert "opaque" not in str(payload)


def test_observation_handles_options_preflight() -> None:
    scope = _build_scope(
        method="OPTIONS",
        path="/clip",
        headers=[
            (b"origin", b"https://app.example.test"),
            (b"access-control-request-method", b"POST"),
        ],
    )

    records = _run_middleware(scope)

    assert records, "expected an OPTIONS preflight to be observed"
    payload = records[0][1]
    assert payload["method"] == "OPTIONS"
    assert payload["path"] == "/clip"


def test_observation_skips_non_http_scopes() -> None:
    scope = {"type": "lifespan"}

    records = _run_middleware(scope)

    assert records == []


def test_observation_failure_does_not_break_request() -> None:
    """Telemetry failures must not propagate to the caller."""
    from edge_telemetry_middleware import EdgeTelemetryMiddleware

    app_invoked: list[bool] = []

    async def fake_app(_scope: Any, _receive: Any, _send: Any) -> None:
        app_invoked.append(True)

    def boom_record(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("event sink down")

    with patch("edge_telemetry_middleware.record", side_effect=boom_record):
        middleware = EdgeTelemetryMiddleware(fake_app)
        asyncio.run(middleware(_build_scope(), _empty_receive, _noop_send))

    assert app_invoked == [True], "downstream app must run even when telemetry fails"
