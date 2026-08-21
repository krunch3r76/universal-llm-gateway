"""Unit tests for park-on-WAKE leg (b) — ``cse_wake_delivery``."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from services.git_integration_worker.cursor_auto.cse_wake_delivery import (
    build_wake_prompt_text,
    deliver_cse_wake,
    is_chat_delivery_capable,
    maybe_deliver_cse_wake,
    pay_wake_unit,
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
    assert calls[0]["timeout"] == 120.0
    body = calls[0]["json"]
    assert body["timeout_s"] == 60
    assert body["purpose"] == "operator-proxy"
    assert body["chat_url"] == "https://claude.ai/chat/abc"
    assert body["registration_id"] == "reg-1"
    assert body["reattach"] is True
    assert "retain_lane" not in body
    assert "status:done" not in body["prompt_text"]


def test_deliver_cse_wake_timeout_is_not_unreachable(monkeypatch):
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:9191")

    def _post(method: str, url: str, *, json=None, timeout: float):
        raise httpx.TimeoutException("timed out")

    result = deliver_cse_wake(
        chat_url="https://claude.ai/chat/abc",
        registration_id="reg-1",
        prompt_text="wake body",
        post=_post,
    )
    assert result["ok"] is False
    assert result["code"] == "cse_session_http_timeout"
    assert result["indeterminate"] is True
    assert "unreachable" not in result["error"]


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


def test_pay_wake_unit_no_debt_still_posts_bus_wake():
    job = AutoJob(
        job_id="j1",
        thread_id="6815",
        turn_number=1,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    wake_result = {"ok": True, "status_code": 200}
    with patch(
        "claude_bundles.cse_session_obligations.get_open_wake_owed",
        return_value=None,
    ), patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.post_operator_wake",
        new_callable=AsyncMock,
        return_value=wake_result,
    ) as mock_wake:
        result = asyncio.run(
            pay_wake_unit(
                job,
                dispatch_id="auto-x",
                request_turn="8",
                closeout_status="complete",
            )
        )
    mock_wake.assert_called_once()
    assert result["code"] == "csr.wake.no_debt_bus_wake"
    assert result["skipped"] is False
    assert result["followup_ok"] is False
    assert result["wake_ok"] is True
    assert result["wake"] == wake_result


def test_pay_wake_unit_debt_followup_ok_skips_bus_wake():
    job = AutoJob(
        job_id="j1",
        thread_id="6655",
        turn_number=8,
        subject="s",
        body="b",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="answer",
        cse_chat_url="https://claude.ai/chat/x",
    )
    obligation = {
        "obligation_id": "wake:6655:8",
        "wake_channel": "chat_delivery",
        "payment": {},
        "status": "open",
    }
    with patch(
        "claude_bundles.cse_session_obligations.get_open_wake_owed",
        return_value=obligation,
    ), patch(
        "claude_bundles.cse_session_obligations.resolve_payment_channel",
        return_value={
            "chat_url": "https://claude.ai/chat/x",
            "registration_id": "reg-6655",
        },
    ), patch(
        "claude_bundles.cse_wake_retain.try_claim_wake_payment",
        return_value=True,
    ), patch(
        "services.git_integration_worker.cursor_auto.cse_wake_delivery.maybe_deliver_cse_wake",
        new_callable=AsyncMock,
        return_value={"ok": True, "send_verified": True},
    ), patch(
        "claude_bundles.cse_wake_retain.release_lane_if_debt_cleared",
    ), patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.post_operator_wake",
        new_callable=AsyncMock,
    ) as mock_wake:
        result = asyncio.run(
            pay_wake_unit(
                job,
                dispatch_id="auto-x",
                request_turn="8",
                closeout_status="complete",
            )
        )
    mock_wake.assert_not_called()
    assert result["followup_ok"] is True
    assert result["code"] == "csr.wake.unit_ok"
