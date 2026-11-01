"""CDP Enter preflight: agent, nonce, operator idle, in-page focus."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cursor_bridge.cdp_composer_input import (
    _enter_preflight_js,
    cdp_dispatch_enter_guarded,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "reason_fragment",
    [
        "enter_wrong_active_agent",
        "enter_nonce_missing",
        "enter_operator_active",
        "enter_composer_not_focused_operator_active",
    ],
)
def test_enter_preflight_js_declares_refusal_reasons(reason_fragment: str) -> None:
    js = _enter_preflight_js(
        selector='[aria-label="Prompt"]',
        expected_agent_id="a1",
        nonce_needle="abc123",
    )
    assert reason_fragment in js


@pytest.mark.asyncio
async def test_cdp_dispatch_enter_guarded_refuses_without_key_events() -> None:
    pre = {"ok": False, "reason": "enter_nonce_missing"}

    async def _fake_eval(_ws: Any, _mid: int, _expr: str) -> Any:
        return pre

    mock_ws = MagicMock()
    mock_ws.send = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("websockets.connect", return_value=mock_cm):
        with patch(
            "cursor_bridge.cdp_composer_input._runtime_eval", side_effect=_fake_eval
        ):
            out = await cdp_dispatch_enter_guarded(
                "ws://x",
                selector='[aria-label="Prompt"]',
                expected_agent_id="agent-1",
                nonce_needle="deadbeef",
            )
    assert out["ok"] is False
    assert out["reason"] == "enter_nonce_missing"
    mock_ws.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_cdp_dispatch_enter_guarded_happy_path_dispatches_enter() -> None:
    sent: list[dict[str, Any]] = []

    async def _fake_eval(_ws: Any, _mid: int, _expr: str) -> Any:
        return {"ok": True, "composer_focused": False}

    async def _fake_send(payload: str) -> None:
        sent.append(json.loads(payload))

    mock_ws = MagicMock()
    mock_ws.send = _fake_send
    recv_msgs = [
        json.dumps({"id": 2, "result": {}}),
        json.dumps({"id": 3, "result": {}}),
    ]

    async def _recv() -> str:
        return recv_msgs.pop(0)

    mock_ws.recv = _recv
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("websockets.connect", return_value=mock_cm):
        with patch(
            "cursor_bridge.cdp_composer_input._runtime_eval", side_effect=_fake_eval
        ):
            out = await cdp_dispatch_enter_guarded(
                "ws://x",
                selector='[aria-label="Prompt"]',
                expected_agent_id="agent-1",
                nonce_needle="cafebabe",
            )
    assert out["ok"] is True
    assert out["method"] == "cdp_enter"
    assert len(sent) == 2
    assert sent[0]["method"] == "Input.dispatchKeyEvent"
    assert sent[0]["params"]["key"] == "Enter"
