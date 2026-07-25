"""Unit matrix for explicit anthropic team_dispatch override admission."""

from __future__ import annotations

from typing import Any

import pytest
from agent_seat import AgentMeta, HydrationBundle

from .admission import FrontierEndpointError
from .anthropic_override_gate import (
    REJECT_CODE,
    enforce_anthropic_override,
    evaluate_anthropic_override,
)
from .service import FrontierGenerateRequest, build_dispatch_body

_OPUS = "anthropic/claude-opus-5"
_DISPATCH_THREAD = "test-dispatch-thread"


def test_reviewer_opus_without_attestation_rejects() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=("openai/gpt-5.6-terra",),
    )
    assert not verdict.admitted
    assert verdict.code == REJECT_CODE


def test_reviewer_opus_with_cost_intent_admits() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
        cost_intent="deliberate_high_cost",
        cost_intent_reason="operator authorized cross-family review",
    )
    assert verdict.admitted


def test_spawn_review_provenance_admits_without_cost_intent() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
        spawn_review_provenance="generate_review_child",
    )
    assert verdict.admitted


def test_in_family_anthropic_profile_admits() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="anthropic",
        profile_allowed_models=(
            "anthropic/claude-opus-5",
            "anthropic/claude-opus-4-8",
            "anthropic/claude-sonnet-4-6",
        ),
    )
    assert verdict.admitted


def test_openai_explicit_model_noops() -> None:
    verdict = evaluate_anthropic_override(
        model="openai/gpt-5.6-terra",
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert verdict.admitted


def test_cursor_explicit_model_noops() -> None:
    verdict = evaluate_anthropic_override(
        model="cursor/claude-sonnet-4-6",
        profile_provider="cursor",
        profile_allowed_models=(),
    )
    assert verdict.admitted


def test_role_allowlist_alone_does_not_authorize_cross_family() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_anthropic_override(
            request_id="req-cross",
            model=_OPUS,
            profile_provider="openai",
            profile_allowed_models=("openai/gpt-5.6-terra",),
        )
    err = exc_info.value
    assert err.code == REJECT_CODE
    assert err.field == "model"
    assert err.status_code == 422


def test_empty_cost_intent_reason_rejects() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
        cost_intent="deliberate_high_cost",
        cost_intent_reason="   ",
    )
    assert not verdict.admitted


def _bundle(meta: AgentMeta) -> HydrationBundle:
    return HydrationBundle(briefing_card_md="# briefing", agent_meta=meta)


@pytest.mark.asyncio
async def test_build_dispatch_body_rejects_reviewer_opus_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_OPUS, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_OPUS,
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        await build_dispatch_body(req)
    assert exc_info.value.code == REJECT_CODE


@pytest.mark.asyncio
async def test_build_dispatch_body_admits_reviewer_opus_with_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hydrate(agent: str, **_k: Any) -> HydrationBundle:
        return _bundle(
            AgentMeta(
                default_model="openai/gpt-5.6-terra",
                allowed_models=[_OPUS, "openai/gpt-5.6-terra"],
                allowed_options=None,
            ),
        )

    monkeypatch.setattr(
        "systems.frontier_consult.service.hydrate_agent",
        fake_hydrate,
    )

    req = FrontierGenerateRequest(
        messages=[{"role": "user", "content": "review this"}],
        role="reviewer",
        dispatch_thread_id=_DISPATCH_THREAD,
        model=_OPUS,
        cost_intent="deliberate_high_cost",
        cost_intent_reason="operator authorized opus review",
    )
    body = await build_dispatch_body(req)
    assert body["pipeline_options"]["model"] == _OPUS
