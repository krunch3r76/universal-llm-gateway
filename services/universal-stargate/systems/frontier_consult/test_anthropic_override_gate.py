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
    set_anthropic_credit_health_probe,
)
from .service import FrontierGenerateRequest, build_dispatch_body

_OPUS = "anthropic/claude-opus-5"
_SONNET55 = "anthropic/claude-sonnet-5-5"
_HAIKU55 = "anthropic/claude-haiku-5-5"
_FABLE = "anthropic/claude-fable-5-1"
_DISPATCH_THREAD = "test-dispatch-thread"


@pytest.fixture(autouse=True)
def _reset_credit_health_probe() -> None:
    set_anthropic_credit_health_probe(lambda: True)
    yield
    set_anthropic_credit_health_probe(None)


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
        model="cursor/claude-sonnet-5",
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


@pytest.mark.parametrize("model", [_SONNET55, _HAIKU55])
def test_standard_model_admitted_when_credit_gate_healthy(model: str) -> None:
    verdict = evaluate_anthropic_override(
        model=model,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert verdict.admitted


def test_opus_still_requires_cost_intent_when_healthy() -> None:
    verdict = evaluate_anthropic_override(
        model=_OPUS,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


def test_fable_still_requires_cost_intent_when_healthy() -> None:
    verdict = evaluate_anthropic_override(
        model=_FABLE,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


def test_standard_model_rejects_when_credit_unhealthy() -> None:
    set_anthropic_credit_health_probe(lambda: False)
    verdict = evaluate_anthropic_override(
        model=_SONNET55,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


def test_standard_model_rejects_when_health_probe_errors() -> None:
    def _boom() -> bool:
        raise TimeoutError("probe down")

    set_anthropic_credit_health_probe(_boom)
    verdict = evaluate_anthropic_override(
        model=_HAIKU55,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


def test_standard_model_rejects_on_invalid_credit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _bad_config() -> None:
        from anthropic_credit.config import CreditConfigInvalid

        raise CreditConfigInvalid("broken")

    monkeypatch.setattr(
        "anthropic_credit.config.load_credit_config",
        _bad_config,
    )
    verdict = evaluate_anthropic_override(
        model=_SONNET55,
        profile_provider="openai",
        profile_allowed_models=(),
    )
    assert not verdict.admitted


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
