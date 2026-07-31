"""Unit tests for notify_pager NotifyResult and life MCP failure reason surfacing."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pager_notify.client import NotifyResult, notify_pager

pytestmark = pytest.mark.offline


class _FakeResp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def post(self, path: str, *, json: dict[str, object]) -> _FakeResp:
        assert path == "/pager/notify"
        assert json
        return self._resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None


@pytest.mark.asyncio
async def test_notify_pager_sent_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pager_notify.client.pager_enabled", lambda: True)
    resp = _FakeResp(payload={"status": "sent"})
    monkeypatch.setattr(
        "pager_notify.client.make_async_client",
        lambda *_a, **_k: _FakeClient(resp),
    )

    result = await notify_pager("ULG test", "body", tag="t")

    assert result.status == "sent"
    assert result.reason == ""
    assert bool(result) is True


@pytest.mark.asyncio
async def test_notify_pager_http_error_surfaces_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pager_notify.client.pager_enabled", lambda: True)
    resp = _FakeResp(status_code=503, text="email-bridge unavailable")
    monkeypatch.setattr(
        "pager_notify.client.make_async_client",
        lambda *_a, **_k: _FakeClient(resp),
    )

    result = await notify_pager("ULG test", "body", tag="t")

    assert result.status == "failed"
    assert result.reason == "HTTP 503"
    assert "email-bridge unavailable" in result.error
    assert bool(result) is False


@pytest.mark.asyncio
async def test_notify_pager_exception_surfaces_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pager_notify.client.pager_enabled", lambda: True)

    class _RaisingClient:
        async def post(self, *_a: object, **_k: object) -> _FakeResp:
            raise TimeoutError("read timed out")

        async def __aenter__(self) -> _RaisingClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(
        "pager_notify.client.make_async_client",
        lambda *_a, **_k: _RaisingClient(),
    )

    result = await notify_pager("ULG test", "body", tag="t")

    assert result.status == "failed"
    assert result.reason == "TimeoutError"
    assert "read timed out" in result.error


@pytest.mark.asyncio
async def test_notify_pager_disabled_returns_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pager_notify.client.pager_enabled", lambda: False)

    result = await notify_pager("ULG test", "body", tag="t")

    assert result.status == "failed"
    assert result.reason == "PAGER_NOTIFY_ENABLED=0"
    assert bool(result) is False


def test_notify_tool_failed_includes_reason() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "mcp-server"))
    from request_profile import bind_request
    from tools.notify import register_notify_tools

    cap_mcp = type("_Cap", (), {"fn": None})()

    def _tool(*_a: object, **_k: object):
        def decorator(fn: object) -> object:
            cap_mcp.fn = fn
            return fn

        return decorator

    cap_mcp.tool = _tool
    register_notify_tools(cap_mcp)
    assert cap_mcp.fn is not None

    failed = NotifyResult.failed("HTTP 503", error="bridge down")
    with bind_request("default", surface="life"):
        with (
            patch("tools.notify.pager_enabled", return_value=True),
            patch(
                "tools.notify.notify_pager",
                new=AsyncMock(return_value=failed),
            ),
        ):
            out = cap_mcp.fn(subject="ULG test", body="awareness ping", ref="agent-bus:1")

    assert out["status"] == "failed"
    assert out["reason"] == "HTTP 503"
    assert out["error"] == "bridge down"
