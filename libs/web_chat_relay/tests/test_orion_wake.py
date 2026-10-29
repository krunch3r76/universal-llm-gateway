"""Offline tests for Orion pointer wakes (fake page, never live chatgpt.com)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bus_watch.endpoint_wake_transport import classify_orion
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_ID,
    PINNED_CONVERSATION_URL,
    build_wake_pointer,
    deliver_orion_wake,
    holder_conversation_url,
)

pytestmark = pytest.mark.offline

PINNED = PINNED_CONVERSATION_URL


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.inserted: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)

    async def insert_text(self, text: str) -> None:
        self.inserted.append(text)


class _FakeLocator:
    def __init__(self, page: "_FakePage", *, visible: bool = True, count: int = 1) -> None:
        self._page = page
        self._visible = visible
        self._count = count

    @property
    def last(self) -> "_FakeLocator":
        return self

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def click(self) -> None:
        self._page.clicked.append("click")

    async def press(self, key: str) -> None:
        await self._page.keyboard.press(key)

    def locator(self, _sel: str) -> "_FakeLocator":
        return _FakeLocator(self._page, visible=True, count=len(self._page.approval_buttons))

    def nth(self, index: int) -> "_FakeLocator":
        self._page.approval_click_index = index
        return self

    async def inner_text(self) -> str:
        idx = self._page.approval_click_index
        buttons = self._page.approval_buttons
        if idx is not None and idx < len(buttons):
            return buttons[idx]
        return ""


class _FakePage:
    def __init__(
        self,
        *,
        url: str = PINNED,
        streaming: bool = False,
        approval_buttons: list[str] | None = None,
        fail_paste: bool = False,
    ) -> None:
        self.url = url
        self.streaming = streaming
        self.approval_buttons = approval_buttons or []
        self.fail_paste = fail_paste
        self.keyboard = _FakeKeyboard()
        self.clicked: list[str] = []
        self.approval_click_index: int | None = None

    def locator(self, selector: str) -> _FakeLocator:
        if "dialog" in selector:
            n = len(self.approval_buttons)
            return _FakeLocator(self, visible=bool(n), count=max(n, 1))
        if "button" in selector and "Send" not in selector:
            n = len(self.approval_buttons) or 1
            return _FakeLocator(self, count=n)
        return _FakeLocator(self)

    async def evaluate(self, expression: str) -> object:
        if "stop" in expression.lower():
            return self.streaming
        if "dialog" in expression:
            if not self.approval_buttons:
                return {"present": False, "buttons": []}
            return {"present": True, "buttons": self.approval_buttons}
        return {}

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


@pytest.mark.asyncio
async def test_happy_path_paste_and_second_approval() -> None:
    page = _FakePage(
        approval_buttons=["Deny", "Allow for this conversation", "Always allow"],
    )
    out = await deliver_orion_wake(
        thread="21207",
        turn=7,
        from_agent="web-anthropic",
        page=page,
    )
    assert out["ok"] is True, out
    assert out["delivered"] is True
    assert out["approval"]["clicked_text"] == "Allow for this conversation"
    assert "21207" in page.keyboard.inserted[0]
    assert "turn 7" in page.keyboard.inserted[0]
    assert "maestro" in page.keyboard.inserted[0].lower() or "web-anthropic" in page.keyboard.inserted[0]


@pytest.mark.asyncio
async def test_conversation_missing_wrong_tab() -> None:
    page = _FakePage(url="https://chatgpt.com/plugins")
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="maestro", page=page
    )
    assert out["reason"] == "conversation_missing"


@pytest.mark.asyncio
async def test_deferred_streaming() -> None:
    page = _FakePage(streaming=True)
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="maestro", page=page
    )
    assert out["reason"] == "deferred_streaming"
    assert out["pasted"] is False


@pytest.mark.asyncio
async def test_orion_echo_drop() -> None:
    page = _FakePage()
    out = await deliver_orion_wake(
        thread="21207", turn=1, from_agent="orion", page=page
    )
    assert out["reason"] == "orion_echo_drop"
    assert not page.keyboard.inserted


@pytest.mark.asyncio
async def test_approval_absent() -> None:
    page = _FakePage(approval_buttons=[])
    out = await deliver_orion_wake(
        thread="21207", turn=2, from_agent="maestro", page=page
    )
    assert out["ok"] is True
    assert out["approval"]["outcome"] == "approval_absent"


@pytest.mark.asyncio
async def test_approval_unrecognized_stops() -> None:
    page = _FakePage(approval_buttons=["Only one button"])
    out = await deliver_orion_wake(
        thread="21207", turn=2, from_agent="maestro", page=page
    )
    assert out["reason"] == "approval_unrecognized"
    assert out["pasted"] is True
    assert out["delivered"] is False


@pytest.mark.asyncio
async def test_cdp_down() -> None:
    async def _boom(_url: str):
        raise ConnectionError("refused")

    out = await deliver_orion_wake(
        thread="21207",
        turn=1,
        from_agent="maestro",
        connect_cdp=_boom,
    )
    assert out["reason"] == "cdp_unreachable"


def test_classify_orion_outcomes() -> None:
    assert classify_orion({"ok": True, "delivered": True, "reason": "delivered"})[0] == "delivered"
    assert classify_orion({"ok": False, "reason": "deferred_streaming"})[0] == "pending"
    assert classify_orion({"ok": False, "reason": "orion_echo_drop"})[0] == "skipped"
    assert classify_orion({"ok": False, "reason": "approval_unrecognized", "pasted": True})[0] == "refused"


def test_holder_conversation_url_from_subscription() -> None:
    sub = {"agent_id": PINNED_CONVERSATION_ID}
    assert PINNED_CONVERSATION_ID in holder_conversation_url(sub)
    assert build_wake_pointer(thread="1", turn=2, from_agent="x").startswith("WAKE —")
