"""S1 supervision envelope on web_tab actuation observation (offline)."""

from __future__ import annotations

import pytest
from web_tab.cdp_port import RecordingCdpPort
from web_tab.observation import observe_actuation_plane

pytestmark = pytest.mark.offline


class FakePlaywrightError(Exception):
    pass


@pytest.mark.asyncio
async def test_actuation_without_target_is_unscanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    port = RecordingCdpPort(tabs=[])
    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9222",
        target_id=None,
        port=port,
        probe_js="() => ({signed_in: true})",
        surface="chatgpt",
    )
    sup = plane["supervision"]
    assert sup["scanned"] is False
    assert sup["ui_error"]["present"] is None
    assert sup["auth_state"] == "unknown"
    assert sup["app_health"] is None


@pytest.mark.asyncio
async def test_actuation_with_banner_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    port = RecordingCdpPort(
        tabs=[
            {
                "id": "tab1",
                "url": "https://chatgpt.com/c/abc",
                "title": "ChatGPT",
            }
        ],
        evaluate_results={
            "tab1": {"signed_in": True},
        },
    )
    port.evaluate_results["tab1-banner"] = {
        "scanned": True,
        "present": True,
        "kind": "session_expired",
        "summary": "Session expired",
    }

    async def _eval(**kwargs: object) -> dict:
        expr = str(kwargs.get("expression") or "")
        if "role='alert'" in expr or "selectors" in expr:
            return {
                "scanned": True,
                "present": True,
                "kind": "session_expired",
                "summary": "Session expired",
            }
        return {"signed_in": True}

    port.evaluate_on_target = _eval  # type: ignore[method-assign]

    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9222",
        target_id="tab1",
        port=port,
        probe_js="() => ({signed_in: true})",
        surface="chatgpt",
    )
    sup = plane["supervision"]
    assert sup["scanned"] is True
    assert sup["auth_state"] == "signed_in"
    assert sup["ui_error"]["present"] is True


@pytest.mark.asyncio
async def test_actuation_banner_evaluate_exception_unscanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "observe")
    port = RecordingCdpPort(
        tabs=[
            {
                "id": "tab1",
                "url": "https://chatgpt.com/c/abc",
                "title": "ChatGPT",
            }
        ],
    )
    eval_calls = {"banner": 0}

    async def _eval(**kwargs: object) -> dict:
        expr = str(kwargs.get("expression") or "")
        if "role='alert'" in expr or "selectors" in expr:
            eval_calls["banner"] += 1
            raise FakePlaywrightError("execution context destroyed")
        return {"signed_in": True}

    port.evaluate_on_target = _eval  # type: ignore[method-assign]

    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9222",
        target_id="tab1",
        port=port,
        probe_js="() => ({signed_in: true})",
        surface="chatgpt",
    )
    assert eval_calls["banner"] == 1
    sup = plane["supervision"]
    assert sup["ui_error"]["scanned"] is False
    assert sup["ui_error"]["present"] is None


@pytest.mark.asyncio
async def test_actuation_gate_off_skips_banner_evaluate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_SUPERVISION_GATE_MODE", "off")
    port = RecordingCdpPort(
        tabs=[
            {
                "id": "tab1",
                "url": "https://chatgpt.com/c/abc",
                "title": "ChatGPT",
            }
        ],
    )
    banner_calls = 0

    async def _eval(**kwargs: object) -> dict:
        nonlocal banner_calls
        expr = str(kwargs.get("expression") or "")
        if "role='alert'" in expr or "selectors" in expr:
            banner_calls += 1
        return {"signed_in": True}

    port.evaluate_on_target = _eval  # type: ignore[method-assign]

    await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9222",
        target_id="tab1",
        port=port,
        probe_js="() => ({signed_in: true})",
        surface="chatgpt",
    )
    assert banner_calls == 0
