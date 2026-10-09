"""Opt-in pointer_only bus bodies omit prompt and harvest text."""

from __future__ import annotations

import asyncio

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.cdp_onbehalf_delivery import (
    build_cdp_success_delivery_body,
)
from systems.frontier_consult.handoff import build_pointer_only_dispatch_pointer

pytestmark = pytest.mark.offline

_MARKER = "PINMARKER-16001-STARGATE"


def test_pointer_only_dispatch_omits_prompt_line() -> None:
    body = build_pointer_only_dispatch_pointer(
        lane="SDK",
        contract="freeform",
        sidecar_ref="cortex://notes/off-bus.txt",
        prompt_sha256="abc",
        correlation_id="exec-1",
    )
    assert _MARKER not in body
    assert "Summary:" not in body
    assert "cortex://notes/off-bus.txt" in body
    assert "sha256 `abc`" in body
    assert "exec-1" in body


def test_pointer_only_cdp_delivery_omits_harvest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _sidecar(**kwargs: object) -> object:
        assert _MARKER in str(kwargs.get("content"))

        class _Side:
            uri = "cortex://notes/harvest.txt"
            sha256 = "def"
            body_chars = 3

        return _Side()

    monkeypatch.setattr(
        "systems.frontier_consult.cdp_onbehalf_delivery.write_cdp_harvest_sidecar",
        _sidecar,
    )
    result = CdpGenerateResult(
        ok=True,
        body=f"reply quotes {_MARKER}",
        execution_id="exec-9",
        satellite_execution_id="sat",
        prompt_uri="cortex://notes/prompt.txt",
        picker_model="opus",
    )
    body = asyncio.run(build_cdp_success_delivery_body(
        result=result,
        thread_id="15790",
        subject="cdp generate — exec-9",
        format_metadata="meta",
        pointer_only=True,
    ))
    assert body is not None
    assert _MARKER not in body
    assert "Summary:" not in body
    assert "cortex://notes/harvest.txt" in body
    assert "def" in body
    assert "exec-9" in body
