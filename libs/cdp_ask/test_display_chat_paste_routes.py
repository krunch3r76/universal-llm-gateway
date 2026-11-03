"""Route tests for POST /v1/display-chat/paste."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app

pytestmark = pytest.mark.offline


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: True,
    )
    return TestClient(create_app())


@pytest.fixture
def emitted_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _capture(event: object) -> None:
        captured.append(
            {"signal": event.signal, "payload": dict(event.payload)}  # type: ignore[attr-defined]
        )

    monkeypatch.setattr("cdp_ask.display_chat_paste.emit", _capture)
    return captured


def test_display_chat_paste_relay_records_hub_post_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When local Grok CDP is down, route relay must stub hub POST (method, path, body)."""
    recorded: list[dict[str, Any]] = []

    class _Resp:
        status_code = 200
        content = (
            b'{"ok": true, "reason": "delivered", "delivered": true, "readback_ok": true}'
        )

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "reason": "delivered",
                "delivered": True,
                "readback_ok": True,
            }

        def raise_for_status(self) -> None:
            return None

    mock_post = AsyncMock(return_value=_Resp())

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *args: object, **kwargs: object) -> _Resp:
            recorded.append(
                {
                    "method": "POST",
                    "url": str(url),
                    "json": kwargs.get("json"),
                }
            )
            return await mock_post(url, *args, **kwargs)

    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.operator_actuation_paste_url",
        lambda: "http://127.0.0.1:9/v1/display-chat/paste",
    )
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.httpx.AsyncClient",
        lambda **_: _FakeClient(),
    )

    payload = {
        "display": "10",
        "app": "grok",
        "agent_id": "a1",
        "text": "hello",
        "grant": "operator",
        "authorization_source": "agent-bus:21614#2",
        "scope": "relay-route",
    }
    with patch("cdp_ask.display_chat_paste.emit", lambda _e: None):
        resp = client.post("/v1/display-chat/paste", json=payload)
    assert resp.status_code == 200
    assert len(recorded) == 1
    assert recorded[0]["method"] == "POST"
    assert recorded[0]["url"].endswith("/v1/display-chat/paste")
    body = recorded[0]["json"]
    assert isinstance(body, dict)
    assert body["display"] == payload["display"]
    assert body["app"] == payload["app"]
    assert body["agent_id"] == payload["agent_id"]
    assert body["text"] == payload["text"]


def test_display_chat_paste_missing_grant_409(
    client: TestClient, emitted_events: list
) -> None:
    resp = client.post(
        "/v1/display-chat/paste",
        json={
            "display": "10",
            "app": "grok",
            "agent_id": "a1",
            "text": "hello",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "grant_or_source_required"
    assert not emitted_events


def test_display_chat_paste_ok_emits_event(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
    hub_paste_http_calls: list,
) -> None:
    deliver = AsyncMock(
        return_value={
            "ok": True,
            "delivered": True,
            "readback_ok": True,
            "display": "10",
            "app": "grok",
            "agent_id": "a1",
            "reason": "delivered",
            "sha256_dom": "d" * 64,
            "sha256_png": "p" * 64,
        }
    )
    monkeypatch.setattr(
        "cdp_ask.display_chat_paste.deliver_operator_paste",
        deliver,
    )

    payload = {
        "display": "10",
        "app": "grok",
        "agent_id": "a1",
        "text": "hello",
        "grant": "operator",
        "authorization_source": "agent-bus:21614#2",
        "scope": "test",
    }
    resp = client.post("/v1/display-chat/paste", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "delivered"
    assert len(emitted_events) == 1
    assert emitted_events[0]["signal"] == "display_chat.pasted"
    assert not hub_paste_http_calls
    deliver.assert_awaited_once()
    call = deliver.await_args
    assert call is not None
    assert call.kwargs["display"] == payload["display"]
    assert call.kwargs["app"] == payload["app"]
    assert call.kwargs["agent_id"] == payload["agent_id"]
    assert call.kwargs["text"] == payload["text"]
    assert call.kwargs["grant"] == payload["grant"]
    assert call.kwargs["authorization_source"] == payload["authorization_source"]
    assert call.kwargs["scope"] == payload["scope"]


def test_display_chat_paste_operator_busy_status(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
    hub_paste_http_calls: list,
) -> None:
    deliver = AsyncMock(
        return_value={
            "ok": False,
            "reason": "operator_busy",
            "submitted": False,
            "pasted": False,
            "display": "10",
            "app": "grok",
            "agent_id": "a1",
        }
    )
    monkeypatch.setattr(
        "cdp_ask.display_chat_paste.deliver_operator_paste",
        deliver,
    )
    payload = {
        "display": "10",
        "app": "grok",
        "agent_id": "a1",
        "text": "hello",
        "grant": "operator",
        "authorization_source": "agent-bus:21614#2",
    }
    resp = client.post("/v1/display-chat/paste", json=payload)
    assert not hub_paste_http_calls
    deliver.assert_awaited_once()
    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "operator_busy"
    assert body["code"] == "operator_busy"
    assert body["submitted"] is False
    assert len(emitted_events) == 1
    assert emitted_events[0]["payload"]["ok"] is False
