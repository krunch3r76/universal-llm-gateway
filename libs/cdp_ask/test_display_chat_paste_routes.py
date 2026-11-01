"""Route tests for POST /v1/display-chat/paste."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app

pytestmark = pytest.mark.offline


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
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
) -> None:
    monkeypatch.setattr(
        "cdp_ask.display_chat_paste.deliver_operator_paste",
        AsyncMock(
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
        ),
    )

    resp = client.post(
        "/v1/display-chat/paste",
        json={
            "display": "10",
            "app": "grok",
            "agent_id": "a1",
            "text": "hello",
            "grant": "operator",
            "authorization_source": "agent-bus:21614#2",
            "scope": "test",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "delivered"
    assert len(emitted_events) == 1
    assert emitted_events[0]["signal"] == "display_chat.pasted"


def test_display_chat_paste_operator_busy_status(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.display_chat_paste.deliver_operator_paste",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "operator_busy",
                "submitted": False,
                "pasted": False,
                "display": "10",
                "app": "grok",
                "agent_id": "a1",
            }
        ),
    )
    resp = client.post(
        "/v1/display-chat/paste",
        json={
            "display": "10",
            "app": "grok",
            "agent_id": "a1",
            "text": "hello",
            "grant": "operator",
            "authorization_source": "agent-bus:21614#2",
        },
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "operator_busy"
    assert body["code"] == "operator_busy"
    assert body["submitted"] is False
    assert len(emitted_events) == 1
    assert emitted_events[0]["payload"]["ok"] is False
