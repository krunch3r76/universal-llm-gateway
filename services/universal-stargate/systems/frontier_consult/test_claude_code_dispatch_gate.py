"""Claude Code (claude_surface=code) dispatch prohibit gate on CDP generate."""

from __future__ import annotations

import pytest
from fastapi import Response

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
from systems.frontier_consult.route import TeamDispatchGenerateBody


@pytest.mark.asyncio
async def test_claude_surface_code_refused_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="22986",
        prompt="code surface probe\n",
        claude_surface="code",
    )
    published: list[object] = []
    monkeypatch.setattr(
        "systems.frontier_consult.claude_code_dispatch_gate.publish_frontier_event",
        lambda event: published.append(event),
    )
    with pytest.raises(FrontierEndpointError) as excinfo:
        await dispatch_cdp_generate(
            request_id="req-cc-prohibit",
            body=body,
            response=Response(),
        )
    err = excinfo.value
    assert err.status_code == 422
    assert err.code == "CLAUDE_CODE_DISPATCH_PROHIBITED"
    assert err.field == "claude_surface"
    assert err.details is not None
    assert err.details.get("code") == "CLAUDE_CODE_DISPATCH_PROHIBITED"
    assert err.details.get("retryable") is False
    assert published


@pytest.mark.asyncio
async def test_claude_surface_code_allowed_when_policy_allow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "implement_admission.claude_code_dispatch.load_claude_code_dispatch_mode",
        lambda *_a, **_k: "allow",
    )
    from systems.frontier_consult.test_cdp_generate import _stub_cdp_generate_after_gate

    _stub_cdp_generate_after_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.record_spawn",
        lambda **_k: {"ok": True},
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="22986",
        prompt="code surface allow\n",
        claude_surface="code",
        caller_agent="cursor-sdk-test",
    )
    result = await dispatch_cdp_generate(
        request_id="req-cc-allow",
        body=body,
        response=Response(),
    )
    assert result.get("execution_id") or result.get("handoff_status") == "ok"


@pytest.mark.asyncio
async def test_cowork_surface_not_gated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from systems.frontier_consult.test_cdp_generate import _stub_cdp_generate_after_gate

    _stub_cdp_generate_after_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.record_spawn",
        lambda **_k: {"ok": True},
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        job="freeform",
        model="cdp/opus-5.5",
        dispatch_thread_id="22986",
        prompt="cowork default\n",
        claude_surface="cowork",
        caller_agent="cursor-sdk-test",
    )
    result = await dispatch_cdp_generate(
        request_id="req-cowork",
        body=body,
        response=Response(),
    )
    assert result.get("execution_id") or result.get("handoff_status") == "ok"
