"""Admission tests for team_dispatch generate/to_thread guards."""

from __future__ import annotations

from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle

from .admission import (
    FrontierEndpointError,
    enforce_team_dispatch_generate_admit,
)
from .service import FrontierGenerateRequest, build_dispatch_body

_DISPATCH_THREAD = "test-dispatch-thread"


def test_g1_claude_web_seat_rejected() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("claude-web", request_id="req-g1")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "web_seat_not_generate_target"
    assert err.field == "role"
    assert "claude-web" in err.reason


def test_g2_lead_role_rejected() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("lead", request_id="req-g2")
    err = exc_info.value
    assert err.code == "web_seat_not_generate_target"
    assert "claude-web" in err.reason


def test_g3_reviewer_role_admitted() -> None:
    enforce_team_dispatch_generate_admit("reviewer", request_id="req-g3")


def test_cursor_lead_role_rejected_on_generate() -> None:
    """cursor-lead resolves to claude-cursor (manual, non-dispatchable) → 422."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("cursor-lead", request_id="req-cl")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "web_seat_not_generate_target"
    assert err.field == "role"
    assert "claude-cursor" in err.reason


def test_implementer_role_rejected_on_generate() -> None:
    """implementer resolves to claude-cursor (handoff-only) → 422 on generate."""
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit("implementer", request_id="req-impl")
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "web_seat_not_generate_target"
    assert "claude-cursor" in err.reason


def _bundle(meta: AgentMeta) -> HydrationBundle:
    return HydrationBundle(briefing_card_md="# briefing", agent_meta=meta)


@pytest.mark.asyncio
async def test_g4_build_dispatch_body_rejects_claude_web_even_with_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(
        agent: str, transcript_id: str | None = None, **_k: Any
    ) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model=None,
                allowed_models=[],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "hello"}],
        role="claude-web",
        dispatch_thread_id=_DISPATCH_THREAD,
        model="anthropic/claude-opus-4-8",
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.code == "web_seat_not_generate_target"
