"""S1 supervision on grok_session probe_dom and chatgpt seat receipts (offline)."""

from __future__ import annotations

from typing import Any

import pytest
from browser_governance.contract import classify_ui_banner_text
from web_chat_relay.grok_session import probe_dom

pytestmark = pytest.mark.offline


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
