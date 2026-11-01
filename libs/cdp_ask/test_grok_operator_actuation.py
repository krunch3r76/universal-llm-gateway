"""Grok operator paste relay when local CDP is absent."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from cdp_ask.display_chat_models import DisplayChatPasteRequest
from cdp_ask.grok_operator_actuation import relay_grok_operator_paste_if_needed

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_relay_skipped_when_local_grok_cdp_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: True,
    )
    req = DisplayChatPasteRequest(
        display="10",
        app="grok",
        agent_id="a1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    assert await relay_grok_operator_paste_if_needed(req) is None


@pytest.mark.asyncio
async def test_relay_forwards_when_local_cdp_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.local_grok_cdp_reachable",
        lambda **_: False,
    )
    monkeypatch.setattr(
        "cdp_ask.grok_operator_actuation.operator_actuation_paste_url",
        lambda: "http://10.0.0.67:8770/v1/display-chat/paste",
    )

    class _Resp:
        status_code = 200
        content = b'{"ok": true, "reason": "delivered", "delivered": true, "readback_ok": true}'

        def json(self) -> dict[str, object]:
            return {"ok": True, "reason": "delivered", "delivered": True, "readback_ok": True}

        def raise_for_status(self) -> None:
            return None

    mock_post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr("cdp_ask.grok_operator_actuation.httpx.AsyncClient", lambda **_: _FakeClient(mock_post))

    req = DisplayChatPasteRequest(
        display="10",
        app="grok",
        agent_id="a1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    out = await relay_grok_operator_paste_if_needed(req)
    assert out is not None
    assert out.ok is True
    mock_post.assert_awaited_once()


class _FakeClient:
    def __init__(self, post: AsyncMock) -> None:
        self._post = post

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> object:
        return await self._post(*args, **kwargs)
