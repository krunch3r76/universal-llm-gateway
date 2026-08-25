"""Route tests for chat_session harvest/probe/paste — monkeypatched adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.chat_session_models import ChatHarvestResponse, ChatPasteResponse

pytestmark = pytest.mark.offline

GROK_ID = "47794c69-9fcc-4481-b1a6-f6c9cbf8b768"
GROK_URL = f"https://grok.com/c/{GROK_ID}"
CSE_URL = "https://claude.ai/cowork/cse_abc123"
CLAUDE_CHAT_URL = "https://claude.ai/chat/thread-uuid"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    app = create_app()
    return TestClient(app)


@pytest.fixture
def emitted_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _capture(event: object) -> None:
        captured.append(
            {
                "signal": event.signal,
                "payload": dict(event.payload),
            }
        )

    monkeypatch.setattr("cdp_ask.chat_session_harvest.emit", _capture)
    monkeypatch.setattr("cdp_ask.chat_session_paste.emit", _capture)
    return captured


def test_cse_url_harvest_409_no_event(client: TestClient, emitted_events: list) -> None:
    resp = client.post(
        "/v1/chat-session/harvest",
        json={"url": CSE_URL},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "use_cse_session"
    assert body["source"] == "gateway"
    assert body["retryable"] is False
    assert "data" in body
    assert not any(e["signal"] == "mcp.chat.session.harvested" for e in emitted_events)


def test_grok_harvest_200_emits_event(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harvest_response = ChatHarvestResponse(
        outcome="harvested",
        site="grok",
        conversation_id=GROK_ID,
        url=GROK_URL,
        archive_uri=f"cortex://notes/system/threads/chat-harvest-grok-{GROK_ID[:12]}.md",
        archive_sha256="a" * 64,
        turn_count=2,
    )

    async def _fake_harvest(**_kwargs: object) -> ChatHarvestResponse:
        return harvest_response

    monkeypatch.setattr(
        "cdp_ask.chat_session_harvest.execute_grok_harvest",
        _fake_harvest,
    )

    resp = client.post(
        "/v1/chat-session/harvest",
        json={"url": GROK_URL},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["archive_uri"] == harvest_response.archive_uri
    assert body["archive_sha256"] == harvest_response.archive_sha256
    assert body["turn_count"] == 2
    harvested = [
        e for e in emitted_events if e["signal"] == "mcp.chat.session.harvested"
    ]
    assert len(harvested) == 1
    assert harvested[0]["payload"]["site"] == "grok"
    assert harvested[0]["payload"]["conversation_id"] == GROK_ID
    assert "registration_id" not in harvested[0]["payload"]


def test_probe_200_no_event(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_harvest(**kwargs: object) -> ChatHarvestResponse:
        assert kwargs.get("metadata_only") is True
        return ChatHarvestResponse(
            outcome="harvested",
            site="grok",
            conversation_id=GROK_ID,
            url=GROK_URL,
            turn_count=1,
            streaming=False,
        )

    monkeypatch.setattr(
        "cdp_ask.chat_session_harvest.execute_grok_harvest",
        _fake_harvest,
    )

    resp = client.post(
        "/v1/chat-session/probe",
        json={"url": GROK_URL},
    )
    assert resp.status_code == 200
    assert resp.json()["turn_count"] == 1
    assert not emitted_events


def test_paste_missing_grant_409(client: TestClient, emitted_events: list) -> None:
    resp = client.post(
        "/v1/chat-session/paste",
        json={"url": GROK_URL, "prompt_text": "hello"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "grant_required"
    assert not any(e["signal"] == "mcp.chat.session.pasted" for e in emitted_events)


def test_paste_relay_lock_fresh_409(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat_harvest.grok_adapter.relay_lock_fresh",
        lambda *_args, **_kwargs: True,
    )

    resp = client.post(
        "/v1/chat-session/paste",
        json={
            "url": GROK_URL,
            "prompt_text": "hello",
            "grant": "operator",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "relay_lock_fresh"
    assert not any(e["signal"] == "mcp.chat.session.pasted" for e in emitted_events)


def test_paste_ok_emits_event(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paste_response = ChatPasteResponse(
        ok=True,
        site="grok",
        conversation_id=GROK_ID,
        url=GROK_URL,
        archive_uri=f"cortex://notes/system/threads/chat-harvest-grok-{GROK_ID[:12]}.md",
        archive_sha256="b" * 64,
        send_verified=True,
        pasted_at="2026-08-25T12:00:00+00:00",
    )

    monkeypatch.setattr(
        "cdp_ask.chat_session_paste.execute_grok_paste",
        AsyncMock(return_value=paste_response),
    )

    resp = client.post(
        "/v1/chat-session/paste",
        json={
            "url": GROK_URL,
            "prompt_text": "hello",
            "grant": "explicit",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["conversation_id"] == GROK_ID
    assert body["archive_uri"] == paste_response.archive_uri
    pasted = [e for e in emitted_events if e["signal"] == "mcp.chat.session.pasted"]
    assert len(pasted) == 1
    assert pasted[0]["payload"]["ok"] is True
    assert pasted[0]["payload"]["archive_sha256"] == paste_response.archive_sha256
    assert "registration_id" not in pasted[0]["payload"]


def test_claude_chat_harvest_200_emits_event(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harvest_response = ChatHarvestResponse(
        outcome="harvested",
        site="claude",
        conversation_id="thread-uuid",
        url=CLAUDE_CHAT_URL,
        archive_uri="cortex://notes/system/threads/chat-harvest-claude-thread-uuid.md",
        archive_sha256="c" * 64,
        turn_count=2,
    )

    async def _fake_harvest(**_kwargs: object) -> ChatHarvestResponse:
        return harvest_response

    monkeypatch.setattr(
        "cdp_ask.chat_session_harvest.execute_claude_harvest",
        _fake_harvest,
    )

    resp = client.post(
        "/v1/chat-session/harvest",
        json={"url": CLAUDE_CHAT_URL},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["archive_uri"] == harvest_response.archive_uri
    assert body["archive_sha256"] == harvest_response.archive_sha256
    assert body["turn_count"] == 2
    harvested = [
        e for e in emitted_events if e["signal"] == "mcp.chat.session.harvested"
    ]
    assert len(harvested) == 1
    assert harvested[0]["payload"]["site"] == "claude"
    assert harvested[0]["payload"]["conversation_id"] == "thread-uuid"


def test_claude_new_harvest_no_conversation_no_event(
    client: TestClient,
    emitted_events: list,
) -> None:
    resp = client.post(
        "/v1/chat-session/harvest",
        json={"url": "https://claude.ai/new"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "no_conversation"
    assert body["conversation_id"] == ""
    assert not any(e["signal"] == "mcp.chat.session.harvested" for e in emitted_events)


def test_claude_paste_ok_emits_event(
    client: TestClient,
    emitted_events: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paste_response = ChatPasteResponse(
        ok=True,
        site="claude",
        conversation_id="thread-uuid",
        url=CLAUDE_CHAT_URL,
        archive_uri="cortex://notes/system/threads/chat-harvest-claude-thread-uuid.md",
        archive_sha256="d" * 64,
        send_verified=True,
        pasted_at="2026-08-25T12:00:00+00:00",
    )

    monkeypatch.setattr(
        "cdp_ask.chat_session_paste.execute_claude_paste",
        AsyncMock(return_value=paste_response),
    )

    resp = client.post(
        "/v1/chat-session/paste",
        json={
            "url": CLAUDE_CHAT_URL,
            "prompt_text": "hello",
            "grant": "operator",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["conversation_id"] == "thread-uuid"
    assert body["archive_uri"] == paste_response.archive_uri
    pasted = [e for e in emitted_events if e["signal"] == "mcp.chat.session.pasted"]
    assert len(pasted) == 1
    assert pasted[0]["payload"]["ok"] is True
    assert pasted[0]["payload"]["archive_sha256"] == paste_response.archive_sha256
