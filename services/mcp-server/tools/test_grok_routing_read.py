"""MCP grok_routing_read thin relay."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from tools.grok_routing_read import (
    fetch_grok_routing_read,
    register_grok_routing_read_tool,
)


def test_module_does_not_import_grok_window_bindings() -> None:
    path = Path(__file__).with_name("grok_routing_read.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    flat: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            flat.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            flat.append(node.module or "")
    assert "cursor_bridge.grok_window_bindings" not in flat
    assert "grok_window_bindings" not in " ".join(flat)


def test_relay_happy_path() -> None:
    payload = {"ok": True, "provenance": {"epoch": 3}}
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response
    with patch("tools.grok_routing_read.make_sync_client", return_value=client):
        out = fetch_grok_routing_read(stargate_url="http://test")
    assert out == payload
    client.get.assert_called_once_with("/v1/grok/window-routing")


def _unreachable_out(*, side_effect: BaseException) -> dict[str, Any]:
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.side_effect = side_effect
    with patch("tools.grok_routing_read.make_sync_client", return_value=client):
        return fetch_grok_routing_read(stargate_url="http://test")


def test_relay_connect_error_retryable() -> None:
    req = httpx.Request("GET", "http://test/v1/grok/window-routing")
    out = _unreachable_out(side_effect=httpx.ConnectError("connection refused", request=req))
    assert out["ok"] is False
    assert out["error"]["code"] == "stargate_unreachable"
    assert out["error"]["retryable"] is True


def test_relay_timeout_retryable() -> None:
    req = httpx.Request("GET", "http://test/v1/grok/window-routing")
    out = _unreachable_out(
        side_effect=httpx.ReadTimeout("timed out", request=req),
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "stargate_unreachable"
    assert out["error"]["retryable"] is True


def test_tool_registers_name() -> None:
    recorded: list[str] = []

    class _Mcp:
        def tool(self, **kwargs):  # type: ignore[no-untyped-def]
            def _wrap(fn):  # type: ignore[no-untyped-def]
                recorded.append(fn.__name__)
                return fn

            return _wrap

    register_grok_routing_read_tool(_Mcp())  # type: ignore[arg-type]
    assert recorded == ["grok_routing_read"]
