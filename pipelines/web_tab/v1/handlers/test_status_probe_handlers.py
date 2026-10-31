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
async def test_probe_handler_ok() -> None:
    handler = WebTabProbeHandler()
    out = await handler.execute(None, _Ctx({"target_id": "x"}))
    data = json.loads(out.raw)
    assert data["ok"] is True
    assert data["read_only"] is True
