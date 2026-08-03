"""Unit tests for park-on-WAKE leg (b) — ``cse_wake_delivery``."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from services.git_integration_worker.cursor_auto.cse_wake_delivery import (
    build_wake_prompt_text,
    deliver_cse_wake,
    is_chat_delivery_capable,
    maybe_deliver_cse_wake,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def test_is_chat_delivery_capable_web_only():
    assert is_chat_delivery_capable("web-anthropic") is True
    assert is_chat_delivery_capable("cursor") is False


def test_deliver_cse_wake_no_identity():
    result = deliver_cse_wake(
        chat_url=None,
        registration_id=None,
        prompt_text="wake",
    )
    assert result == {"ok": False, "error": "no_identity", "skipped": True}


def test_deliver_cse_wake_posts_followups_once(monkeypatch):
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:9191")
    calls: list[dict] = []

    def _post(method: str, url: str, *, json=None, timeout: float):
        calls.append({"method": method, "url": url, "json": json, "timeout": timeout})
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.content = b'{"ok": true, "send_verified": true}'
        resp.json.return_value = {"ok": True, "send_verified": True}
        resp.text = ""
        return resp

    result = deliver_cse_wake(
        chat_url="https://claude.ai/chat/abc",
        registration_id="reg-1",
        prompt_text="wake body",
        post=_post,
    )
    assert result["ok"] is True
    assert result["send_verified"] is True
    assert len(calls) == 1
    assert calls[0]["url"] == "http://127.0.0.1:9191/v1/project-ask/followups"
    body = calls[0]["json"]
    assert body["purpose"] == "operator-proxy"
    assert body["chat_url"] == "https://claude.ai/chat/abc"
    assert body["registration_id"] == "reg-1"
    assert body["reattach"] is True
    assert "status:done" not in body["prompt_text"]


def test_build_wake_prompt_text_token_free():
    text = build_wake_prompt_text(
        dispatch_id="auto-abc",
        thread_id="6661",
        request_turn="8",
        closeout_status="complete",
    )
    assert "auto-abc" in text
    assert "status:done" not in text


def test_maybe_deliver_skips_ide_class():
    import asyncio

    job = AutoJob(
        job_id="j1",
        thread_id="1",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="cursor",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
        cse_chat_url="https://claude.ai/chat/x",
    )
    result = asyncio.run(
        maybe_deliver_cse_wake(
            job,
            dispatch_id="auto-x",
            request_turn="1",
            closeout_status="complete",
        )
    )
    assert result["skipped"] is True
    assert result["reason"] == "not_chat_delivery_capable"
