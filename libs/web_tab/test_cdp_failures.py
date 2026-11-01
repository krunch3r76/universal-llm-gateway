"""B1: production Playwright attach failures, timeouts, and observation mapping."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import playwright.async_api as pw_async

from web_tab import cdp_port as cdp_port_mod
from web_tab.cdp_port import HttpListCdpPort
from web_tab.config import resolve_cdp_evaluate_timeout_s
from web_tab.observation import observe_actuation_plane


class _FakeChromium:
    def __init__(self, connect: Any) -> None:
        self._connect = connect

    async def connect_over_cdp(self, cdp_url: str) -> Any:
        return await self._connect(cdp_url)


class _FakePlaywrightDriver:
    def __init__(self, *, connect: Any) -> None:
        self.chromium = _FakeChromium(connect)
        self.stop_awaited = False

    async def stop(self) -> None:
        self.stop_awaited = True


class _FakePlaywrightFactory:
    def __init__(self, *, connect: Any) -> None:
        self._connect = connect
        self.last_driver: _FakePlaywrightDriver | None = None

    def __call__(self) -> _FakePlaywrightFactory:
        return self

    async def start(self) -> _FakePlaywrightDriver:
        driver = _FakePlaywrightDriver(connect=self._connect)
        self.last_driver = driver
        return driver


def _patch_playwright(
    monkeypatch: pytest.MonkeyPatch, connect: Any
) -> _FakePlaywrightFactory:
    factory = _FakePlaywrightFactory(connect=connect)

    class _Shim:
        def __call__(self) -> _FakePlaywrightFactory:
            return factory

    monkeypatch.setattr(pw_async, "async_playwright", _Shim())
    return factory


_TAB = {
    "id": "t1",
    "url": "https://chatgpt.com/c/abc",
}


@pytest.mark.asyncio
async def test_connect_raises_stops_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _connect(cdp_url: str) -> Any:  # noqa: ARG001
        raise ConnectionError("ws rejected")

    factory = _patch_playwright(monkeypatch, _connect)
    port = HttpListCdpPort()
    raw = await port.evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        expression="() => ({})",
        tab_row=dict(_TAB),
    )
    assert raw["error"]["code"] == "cdp_attach_failed"
    assert raw["error"]["type"] == "ConnectionError"
    assert factory.last_driver is not None
    assert factory.last_driver.stop_awaited is True


@pytest.mark.asyncio
async def test_connect_blocks_times_out_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()

    async def _connect(cdp_url: str) -> Any:  # noqa: ARG001
        await gate.wait()
        return None

    factory = _patch_playwright(monkeypatch, _connect)
    monkeypatch.setattr(cdp_port_mod, "resolve_cdp_evaluate_timeout_s", lambda: 0.05)  # noqa: E501
    port = HttpListCdpPort()
    raw = await port.evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        expression="() => ({})",
        tab_row=dict(_TAB),
    )
    assert raw["error"]["code"] == "cdp_timeout"
    assert factory.last_driver is not None
    assert factory.last_driver.stop_awaited is True


@pytest.mark.asyncio
async def test_evaluate_blocks_times_out_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Page:
        url = _TAB["url"]
        context = None

        async def evaluate(self, expression: str) -> dict:  # noqa: ARG002
            await asyncio.sleep(60)
            return {}

    class _Ctx:
        pages = [_Page()]

    class _Browser:
        contexts = [_Ctx()]

    async def _connect(cdp_url: str) -> _Browser:  # noqa: ARG001
        return _Browser()

    async def _target_id_of(page: Any) -> str:
        return "t1"

    factory = _patch_playwright(monkeypatch, _connect)
    monkeypatch.setattr(cdp_port_mod, "_target_id_of", _target_id_of)
    monkeypatch.setattr(cdp_port_mod, "resolve_cdp_evaluate_timeout_s", lambda: 0.05)  # noqa: E501
    port = HttpListCdpPort()
    raw = await port.evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        expression="() => ({})",
        tab_row=dict(_TAB),
    )
    assert raw["error"]["code"] == "cdp_timeout"
    assert factory.last_driver is not None
    assert factory.last_driver.stop_awaited is True


@pytest.mark.asyncio
async def test_mismatch_returns_stale_without_browser_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class _Browser:
        contexts: list = []

        async def close(self) -> None:
            nonlocal closed
            closed = True

    async def _connect(cdp_url: str) -> _Browser:  # noqa: ARG001
        return _Browser()

    factory = _patch_playwright(monkeypatch, _connect)
    port = HttpListCdpPort()
    raw = await port.evaluate_on_target(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        expression="() => ({})",
        tab_row={"id": "t1", "url": "https://chatgpt.com/c/other"},
    )
    assert raw.get("stale") is True
    assert closed is False
    assert factory.last_driver is not None
    assert factory.last_driver.stop_awaited is True


@pytest.mark.asyncio
async def test_external_cancellation_cleans_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def _connect(cdp_url: str) -> Any:  # noqa: ARG001
        entered.set()
        await asyncio.sleep(3600)
        return None

    factory = _patch_playwright(monkeypatch, _connect)
    monkeypatch.setattr(cdp_port_mod, "resolve_cdp_evaluate_timeout_s", lambda: 30.0)
    port = HttpListCdpPort()

    async def _run() -> None:
        await port.evaluate_on_target(
            cdp_url="http://127.0.0.1:9342",
            target_id="t1",
            expression="() => ({})",
            tab_row=dict(_TAB),
        )

    task = asyncio.create_task(_run())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert factory.last_driver is not None
    assert factory.last_driver.stop_awaited is True


@pytest.mark.asyncio
async def test_observation_maps_attach_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _connect(cdp_url: str) -> Any:  # noqa: ARG001
        raise OSError("attach failed")

    _patch_playwright(monkeypatch, _connect)
    port = HttpListCdpPort()

    def _list(*, cdp_url: str) -> dict:  # noqa: ARG001
        return {
            "pages": [dict(_TAB)],
            "reachable": True,
            "error": None,
        }

    port.list_tabs = _list  # type: ignore[method-assign]
    plane = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        port=port,
        probe_js="() => ({})",
    )
    assert plane["cdp_reachable"]["cdp_reachable"] is True
    assert plane["signed_in"]["signed_in"] is None
    assert plane["signed_in"]["source"] == "cdp.error"
    assert plane["signed_in"].get("stale") is not True
    assert plane["error"]["code"] == "cdp_attach_failed"


def test_invalid_timeout_config_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_TAB_CDP_TIMEOUT_S", "-1")
    assert resolve_cdp_evaluate_timeout_s() == 10.0
    monkeypatch.setenv("WEB_TAB_CDP_TIMEOUT_S", "nan")
    assert resolve_cdp_evaluate_timeout_s() == 10.0
    monkeypatch.setenv("WEB_TAB_CDP_TIMEOUT_S", "not-a-number")
    assert resolve_cdp_evaluate_timeout_s() == 10.0
