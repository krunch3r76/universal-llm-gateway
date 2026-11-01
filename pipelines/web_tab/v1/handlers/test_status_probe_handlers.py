"""Pipeline handler smoke tests (hermetic)."""

from __future__ import annotations

import json

import pytest
from web_tab.cdp_port import RecordingCdpPort

from .probe import WebTabProbeHandler
from .status import WebTabStatusHandler


class _Ctx:
    def __init__(self, options: dict) -> None:
        self.options = options


@pytest.mark.asyncio
async def test_status_handler_requires_house() -> None:
    handler = WebTabStatusHandler()
    out = await handler.execute(None, _Ctx({}))
    data = json.loads(out.raw)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_probe_handler_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    port = RecordingCdpPort(
        tabs=[{"id": "x", "url": "https://chatgpt.com/c/x", "title": ""}],
        evaluate_results={"x": {"signed_in": True}},
    )
    monkeypatch.setattr(
        "web_tab.probe.default_cdp_port",
        lambda: port,
    )
    handler = WebTabProbeHandler()
    out = await handler.execute(
        None,
        _Ctx({"target_id": "x", "cdp_url": "http://127.0.0.1:9342"}),
    )
    data = json.loads(out.raw)
    assert data["ok"] is True
    assert data["read_only"] is True
