"""S1 supervision on grok_session probe_dom and chatgpt seat receipts (offline)."""

from __future__ import annotations

from typing import Any

import pytest
from browser_governance.contract import classify_ui_banner_text
from web_chat_relay.chatgpt_com_seat import _supervision_for_chatgpt_page
from web_chat_relay.grok_session import probe_dom

pytestmark = pytest.mark.offline


class FakePlaywrightError(Exception):
    pass


class _FakePage:
    def __init__(self, *, probe: dict[str, Any], banner: dict[str, Any] | None) -> None:
        self._probe = probe
        self._banner = banner

    async def evaluate(self, js: str) -> Any:
        if "sign in to request access" in js or "body_start" in js:
            return self._probe
        return self._banner


@pytest.mark.asyncio
async def test_grok_probe_dom_unscanned_when_banner_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    page = _FakePage(
        probe={"login_wall": False, "composer_count": 1},
        banner=None,
    )
    out = await probe_dom(page)
    sup = out["supervision"]
    assert sup["scanned"] is False
    assert sup["ui_error"]["present"] is None
    assert sup["auth_state"] == "unknown"


@pytest.mark.asyncio
async def test_grok_probe_dom_banner_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    page = _FakePage(
        probe={"login_wall": True},
        banner={
            "scanned": True,
            "present": True,
            "kind": "rate_or_usage_limit",
            "summary": "Usage limit reached",
        },
    )
    out = await probe_dom(page)
    sup = out["supervision"]
    assert sup["ui_error"]["present"] is True
    assert sup["ui_error"]["kind"] == "rate_or_usage_limit"


def test_benign_banner_text_not_classified() -> None:
    assert classify_ui_banner_text("All systems normal on this page") == (None, None)


class _RaisingPage:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, _js: str) -> Any:
        self.calls += 1
        raise FakePlaywrightError("page closed")


@pytest.mark.asyncio
async def test_grok_probe_dom_evaluate_exception_unscanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")

    class _Page:
        async def evaluate(self, js: str) -> Any:
            if "sign in to request access" in js or "body_start" in js:
                return {"login_wall": False, "composer_count": 1}
            raise FakePlaywrightError("destroyed")

    out = await probe_dom(_Page())
    sup = out["supervision"]
    assert sup["scanned"] is False
    assert sup["ui_error"]["present"] is None


@pytest.mark.asyncio
async def test_grok_gate_off_zero_banner_evaluate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    page = _FakePage(probe={"login_wall": False}, banner={"scanned": True})
    banner_calls = 0
    orig = page.evaluate

    async def _eval(js: str) -> Any:
        nonlocal banner_calls
        if "role='alert'" in js or "selectors" in js:
            banner_calls += 1
        return await orig(js)

    page.evaluate = _eval  # type: ignore[method-assign]
    out = await probe_dom(page)
    assert banner_calls == 0
    assert out["supervision"]["reason"] == "gate_mode_off"


@pytest.mark.asyncio
async def test_chatgpt_supervision_evaluate_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    page = _RaisingPage()
    env = await _supervision_for_chatgpt_page(page)  # type: ignore[arg-type]
    assert env["scanned"] is False
    assert env["ui_error"]["present"] is None
