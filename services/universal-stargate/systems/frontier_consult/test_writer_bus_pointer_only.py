"""Opt-in pointer_only bus bodies omit prompt and harvest text."""

from __future__ import annotations

import asyncio

import pytest
from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.cdp_onbehalf_delivery import (
    build_cdp_success_delivery_body,
)
from systems.frontier_consult.handoff import (
    build_pointer_only_dispatch_pointer,
    build_pointer_only_worker_message,
)

pytestmark = pytest.mark.offline

_MARKER = "PINMARKER-16001-STARGATE"
_BASE = "410431e8465e3a65886be7817c2c3a9f26a7ef84"
_TIP = "b630d614b3b58490bc7b43644212470036565ae0"
_CANON = (
    f"VERDICT: APPROVE range={_BASE}..{_TIP} head={_TIP}"
)
_OTHER_TIP = "743839e84a2fb311a67df94f205ea391a2680aae"
_CANON_OTHER = (
    f"VERDICT: REQUEST_CHANGES range={_BASE}..{_OTHER_TIP} head={_OTHER_TIP}"
)


def _inline_delivery_body(harvest: str) -> str:
    result = CdpGenerateResult(
        ok=True,
        body=harvest,
        execution_id="exec-verdict-dedupe",
        satellite_execution_id="sat",
        prompt_uri="cortex://notes/prompt.txt",
        picker_model="opus",
    )
    body = asyncio.run(
        build_cdp_success_delivery_body(
            result=result,
            thread_id="19848",
            subject="cdp reply — exec-verdict-dedupe",
            format_metadata="# CDP generate result",
            pointer_only=False,
        )
    )
    assert body is not None
    return body


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


def test_pointer_only_worker_message_reads_and_verifies_sidecar() -> None:
    message = build_pointer_only_worker_message(
        sidecar_ref="cortex://notes/off-bus.txt",
        prompt_sha256="abc",
    )
    assert _MARKER not in message
    assert "cortex://notes/off-bus.txt" in message
    assert "abc" in message
    assert 'fs(op="read"' in message
    assert "sha256" in message
    assert "cannot be read" in message


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
    body = asyncio.run(
        build_cdp_success_delivery_body(
            result=result,
            thread_id="15790",
            subject="cdp generate — exec-9",
            format_metadata="meta",
            pointer_only=True,
        )
    )
    assert body is not None
    assert _MARKER not in body
    assert "Summary:" not in body
    assert "cortex://notes/harvest.txt" in body
    assert "def" in body
    assert "exec-9" in body


def test_identical_unfenced_canonical_verdict_lines_collapse_to_one() -> None:
    harvest = f"tool badge\n{_CANON}\n\n{_CANON}\n"
    body = _inline_delivery_body(harvest)
    assert body.count(_CANON) == 1


def test_differing_canonical_verdict_lines_both_remain() -> None:
    harvest = f"{_CANON}\n{_CANON_OTHER}\n"
    body = _inline_delivery_body(harvest)
    assert _CANON in body
    assert _CANON_OTHER in body


def test_fenced_canonical_verdict_not_collapsed_with_unfenced() -> None:
    harvest = f"```\n{_CANON}\n```\n{_CANON}\n"
    body = _inline_delivery_body(harvest)
    assert body.count(_CANON) == 2
