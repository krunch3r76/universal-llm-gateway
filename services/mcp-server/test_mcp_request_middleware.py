"""Regression tests for MCP request telemetry helpers."""

from __future__ import annotations


def test_suspected_fs_timeout_matches_long_tiny_fs_response() -> None:
    from mcp_request_middleware import _is_suspected_fs_timeout

    assert _is_suspected_fs_timeout("fs", 30.101, 90)


def test_suspected_fs_timeout_ignores_normal_responses() -> None:
    from mcp_request_middleware import _is_suspected_fs_timeout

    assert not _is_suspected_fs_timeout("fs", 0.5, 90)
    assert not _is_suspected_fs_timeout("fs", 30.101, 320)
    assert not _is_suspected_fs_timeout("cortex", 30.101, 90)


def test_seat_class_life_path() -> None:
    from mcp_request_middleware import _seat_class_from_path

    assert _seat_class_from_path("/mcp/life") == "claude"


def test_seat_class_code_path() -> None:
    from mcp_request_middleware import _seat_class_from_path

    assert _seat_class_from_path("/mcp/code") == "cursor"


def test_seat_class_legacy_bare_mcp() -> None:
    from mcp_request_middleware import _seat_class_from_path

    assert _seat_class_from_path("/mcp") == "claude"


def test_seat_class_unknown_path() -> None:
    from mcp_request_middleware import _seat_class_from_path

    assert _seat_class_from_path("/health") == "unknown"
    assert _seat_class_from_path("/") == "unknown"
    assert _seat_class_from_path("/mcp-other") == "unknown"


import json
from typing import Any
import pytest

from tool_access import ANTHROPIC_API_CLIENT_ID, GROK_CONNECTOR_CLIENT_ID, OPEN_WEBUI_CLIENT_ID, _surface_spec


def _tools_call_payload(*, op: str, tool: str = "fs") -> bytes:
    body = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": {"op": op, "path": "cortex://notes/x.md"},
        },
    }
    return json.dumps(body).encode()


def _split_http_messages(body: bytes) -> list[dict[str, Any]]:
    pivot = max(1, len(body) // 2)
    return [
        {"type": "http.request", "body": body[:pivot], "more_body": True},
        {"type": "http.request", "body": body[pivot:], "more_body": False},
    ]


async def _drive_middleware(
    *,
    oauth_client_id: str,
    http_messages: list[dict[str, Any]],
    content_length: str | None = None,
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    from mcp_request_middleware import McpRequestEventsMiddleware

    app_calls = 0
    post_replay_messages: list[dict[str, Any]] = []
    queue = list(http_messages)

    async def receive() -> dict[str, Any]:
        if queue:
            return queue.pop(0)
        message = {"type": "http.disconnect"}
        return message

    async def app(scope: dict[str, Any], app_receive: Any, app_send: Any) -> None:
        nonlocal app_calls
        app_calls += 1
        while True:
            message = await app_receive()
            post_replay_messages.append(message)
            if message.get("type") == "http.disconnect":
                break

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/code",
        "headers": headers,
        "client": ("127.0.0.1", 5151),
        "oauth_client_id": oauth_client_id,
        "auth_mode": "static",
        "mcp_profile": "default",
    }

    middleware = McpRequestEventsMiddleware(app)
    await middleware(scope, receive, send)
    return app_calls, sent, post_replay_messages


@pytest.mark.asyncio
async def test_two_message_write_allowed_for_anthropic_api() -> None:
    body = _tools_call_payload(op="write")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=ANTHROPIC_API_CLIENT_ID,
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 1


@pytest.mark.asyncio
async def test_two_message_write_denied_for_grok_connector() -> None:
    body = _tools_call_payload(op="write")
    app_calls, sent, _post = await _drive_middleware(
        oauth_client_id=GROK_CONNECTOR_CLIENT_ID,
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 0
    assert any(m.get("type") == "http.response.start" for m in sent)


@pytest.mark.asyncio
async def test_two_message_read_allowed_for_anthropic_api() -> None:
    body = _tools_call_payload(op="read")
    app_calls, _sent, post = await _drive_middleware(
        oauth_client_id=ANTHROPIC_API_CLIENT_ID,
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 1
    assert len(post) == 3
    assert post[-1]["type"] == "http.disconnect"


@pytest.mark.asyncio
async def test_single_message_read_allowed_for_anthropic_api() -> None:
    body = _tools_call_payload(op="read")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=ANTHROPIC_API_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": body, "more_body": False}],
    )
    assert app_calls == 1


@pytest.mark.asyncio
async def test_two_message_write_not_denied_without_oauth_client_id() -> None:
    body = _tools_call_payload(op="write")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id="",
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 1


@pytest.mark.asyncio
async def test_two_message_read_allowed_for_claude_ai() -> None:
    body = _tools_call_payload(op="read")
    app_calls, _sent, post = await _drive_middleware(
        oauth_client_id="claude-ai",
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 1
    assert len(post) == 3
    assert post[-1]["type"] == "http.disconnect"


@pytest.mark.asyncio
async def test_non_json_denied_for_grok_connector() -> None:
    app_calls, sent, _post = await _drive_middleware(
        oauth_client_id=GROK_CONNECTOR_CLIENT_ID,
        http_messages=[
            {"type": "http.request", "body": b"not-json", "more_body": False}
        ],
    )
    assert app_calls == 0
    assert any(m.get("type") == "http.response.start" for m in sent)


@pytest.mark.asyncio
async def test_body_over_cap_returns_413_without_calling_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_request_middleware as mrm

    monkeypatch.setattr(mrm, "DEFAULT_MAX_REQUEST_BODY_SIZE", 8)
    body = _tools_call_payload(op="read")
    app_calls, sent, _post = await _drive_middleware(
        oauth_client_id=ANTHROPIC_API_CLIENT_ID,
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 0
    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert int(start.get("status") or 0) == 413


@pytest.mark.asyncio
async def test_body_over_cap_413_for_claude_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_request_middleware as mrm

    monkeypatch.setattr(mrm, "DEFAULT_MAX_REQUEST_BODY_SIZE", 8)
    body = _tools_call_payload(op="read")
    app_calls, sent, _post = await _drive_middleware(
        oauth_client_id="claude-ai",
        http_messages=_split_http_messages(body),
    )
    assert app_calls == 0
    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert int(start.get("status") or 0) == 413


@pytest.mark.asyncio
async def test_content_length_over_cap_413_without_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_request_middleware as mrm

    monkeypatch.setattr(mrm, "DEFAULT_MAX_REQUEST_BODY_SIZE", 8)
    body = _tools_call_payload(op="read")
    app_calls, sent, _post = await _drive_middleware(
        oauth_client_id=ANTHROPIC_API_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": body, "more_body": False}],
        content_length=str(len(body)),
    )
    assert app_calls == 0
    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert int(start.get("status") or 0) == 413


@pytest.mark.asyncio
async def test_oversize_chunk_not_retained_in_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_request_middleware as mrm

    monkeypatch.setattr(mrm, "DEFAULT_MAX_REQUEST_BODY_SIZE", 10)
    first = b"0123456789"
    second = b"X"
    app_calls, sent, _post = await _drive_middleware(
        oauth_client_id=ANTHROPIC_API_CLIENT_ID,
        http_messages=[
            {"type": "http.request", "body": first, "more_body": True},
            {"type": "http.request", "body": second, "more_body": False},
        ],
    )
    assert app_calls == 0
    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert int(start.get("status") or 0) == 413


def _denied_signal(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import mcp_request_middleware as mrm

    signals: list[str] = []

    def _record(signal: str, **_payload: object) -> None:
        signals.append(signal)

    monkeypatch.setattr(mrm, "record", _record)
    return signals


@pytest.mark.asyncio
async def test_open_webui_denies_write_and_unlisted_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals = _denied_signal(monkeypatch)
    write = _tools_call_payload(op="write")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": write, "more_body": False}],
    )
    assert app_calls == 0
    assert "mcp.oauth.client.tool.denied" in signals

    bus = _tools_call_payload(op="post", tool="agent_bus")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": bus, "more_body": False}],
    )
    assert app_calls == 0

    rag = _tools_call_payload(op="upsert_article", tool="rag")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": rag, "more_body": False}],
    )
    assert app_calls == 0

    families = _surface_spec("code").families
    write_op = next(op for op, fam in families.items() if fam != "read")
    body = json.loads(_tools_call_payload(op="unused", tool="cortex"))
    body["params"]["arguments"] = {"tool": write_op}
    assert families.get(write_op) != "read"
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[
            {
                "type": "http.request",
                "body": json.dumps(body).encode(),
                "more_body": False,
            }
        ],
    )
    assert app_calls == 0

    empty = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "", "arguments": {}},
    }
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[
            {
                "type": "http.request",
                "body": json.dumps(empty).encode(),
                "more_body": False,
            }
        ],
    )
    assert app_calls == 0


@pytest.mark.asyncio
async def test_open_webui_allows_read_surface() -> None:
    rag = _tools_call_payload(op="search", tool="rag")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": rag, "more_body": False}],
    )
    assert app_calls == 1

    read = _tools_call_payload(op="read")
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[{"type": "http.request", "body": read, "more_body": False}],
    )
    assert app_calls == 1

    read_op = next(
        op for op, fam in _surface_spec("code").families.items() if fam == "read"
    )
    assert _surface_spec("code").families.get(read_op) == "read"
    cortex = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "cortex", "arguments": {"tool": read_op}},
    }
    app_calls, _sent, _post = await _drive_middleware(
        oauth_client_id=OPEN_WEBUI_CLIENT_ID,
        http_messages=[
            {
                "type": "http.request",
                "body": json.dumps(cortex).encode(),
                "more_body": False,
            }
        ],
    )
    assert app_calls == 1
