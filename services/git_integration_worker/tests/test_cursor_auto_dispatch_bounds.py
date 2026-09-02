"""Falsifier: the autonomous lane must not commission unbounded premium work.

Auto POSTs the cursor-sdk worker directly, so Stargate's ``sdk_cost_risk`` guard
never sees these binds. Two policy bounds stand in for it — an executor bound (a
reasoning model never runs the mechanical leg) and a scope bound (a ``contract:``
override stops waiving the empty-scope refusal outside the roaming tier) — plus
a card bound (effort rungs the model card does not accept degrade). These tests
pin all three, and pin that the roaming tier is untouched so the default
mechanical path keeps its latitude.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_gates import blocking_admit_gate
from implement_admission.workflow_registry import MECHANICAL_WORKFLOW, load_workflow_registry
from services.git_integration_worker.cursor_auto.dispatch_bounds import (
    clamp_effort_to_model_card,
    is_roaming_tier,
    redirect_mechanical_executor,
    scope_waiver_allowed,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_desired_model,
    resolve_handoff_contract,
)

_NO_SCOPE_WITH_OVERRIDE = (
    "TYPE: DIRECTIVE\ndensity: dense\ncontract: implement\n"
    "vision: mechanical — dispatch bounds fixture\n"
)


@pytest.fixture(autouse=True)
def _capture_events(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, object]]]:
    emitted: list[tuple[str, dict[str, object]]] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append((signal, dict(payload)))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.record",
        _capture,
    )
    return emitted


def _job(desired_model: str) -> AutoJob:
    return AutoJob(
        job_id=f"j-{desired_model}",
        thread_id="5899",
        turn_number=1,
        subject="bounds",
        body=_NO_SCOPE_WITH_OVERRIDE,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model=desired_model,
        desired_effort="medium",
        contract="implement",
    )


def _effort(value: str) -> dict[str, object]:
    return {
        "requested": value,
        "resolved_effort": value,
        "clamped": False,
        "notes": "honored",
    }


@pytest.mark.parametrize(
    "model_id",
    ["composer-2.5", "cursor/composer-2.5", "grok-4.6", "cursor/grok-4.6"],
)
def test_roaming_tier_membership(model_id: str) -> None:
    assert is_roaming_tier(model_id)
    assert scope_waiver_allowed(model_id)


@pytest.mark.parametrize(
    "model_id",
    ["cursor/claude-opus-5", "claude-opus-5", "cursor/gpt-5.6-terra", "", None],
)
def test_non_roaming_models_lose_the_scope_waiver(model_id: str | None) -> None:
    assert not is_roaming_tier(model_id)
    assert not scope_waiver_allowed(model_id)


def test_non_roaming_when_bare_id_not_in_registry() -> None:
    from implement_admission.workflow_registry import ModelPolicy, WorkflowRegistry

    reg = load_workflow_registry()
    stripped_models = {
        bare: ModelPolicy(bare_id=bare, roaming=False)
        for bare, policy in reg.models.items()
        if bare != "grok-4.6"
    }
    reg_without_grok = WorkflowRegistry(
        workflows=reg.workflows,
        models=stripped_models,
        contract_effort=reg.contract_effort,
    )
    assert not is_roaming_tier("cursor/grok-4.6", registry=reg_without_grok)


@pytest.mark.parametrize("requested", ["low", "medium", "high", "xhigh", "max"])
def test_opus_card_accepts_full_effort_ladder(requested: str) -> None:
    payload = _effort(requested)
    assert clamp_effort_to_model_card("cursor/claude-opus-5", payload) is payload


@pytest.mark.parametrize("requested", ["low", "medium", "high", "xhigh"])
def test_grok_card_accepts_through_xhigh(requested: str) -> None:
    payload = _effort(requested)
    assert clamp_effort_to_model_card("cursor/grok-4.6", payload) is payload


def test_grok_max_degrades_to_card_ceiling() -> None:
    out = clamp_effort_to_model_card("cursor/grok-4.6", _effort("max"))
    assert out["resolved_effort"] == "xhigh"
    assert out["clamped"] is True
    assert "not on grok-4.6 card" in str(out["notes"])


@pytest.mark.parametrize(
    "model_id", ["cursor/claude-sonnet-5", "claude-sonnet-5", "sonnet-5"]
)
@pytest.mark.parametrize("requested", ["high", "xhigh", "max"])
def test_sonnet_5_card_accepts_max(model_id: str, requested: str) -> None:
    payload = _effort(requested)
    assert clamp_effort_to_model_card(model_id, payload) is payload


def test_off_ladder_effort_falls_to_card_default() -> None:
    """Off-ladder tokens must not fail open (leave resolved) or drop the knob."""
    from services.git_integration_worker.cursor_auto.knob_compose import (
        compose_model_knobs,
    )

    payload = _effort("none")
    clamped = clamp_effort_to_model_card("cursor/grok-4.6", payload)
    knobs = compose_model_knobs({"resolved_model_id": "cursor/grok-4.6"}, payload)
    assert clamped["resolved_effort"] == "high"
    assert clamped["clamped"] is True
    assert "off-ladder" in str(clamped["notes"])
    assert knobs["effort"] == "high"
    assert knobs["fast"] == "false"


def test_sdk_card_clamp_does_not_define_cdp_wire_effort() -> None:
    """Document the split: sdk knobs follow the card; CDP commission uses wire."""
    from services.git_integration_worker.cursor_auto.wire_map import (
        resolve_desired_effort,
    )

    wire = resolve_desired_effort("max")
    clamped = clamp_effort_to_model_card("cursor/grok-4.6", wire)
    assert wire["resolved_effort"] == "max"
    assert clamped["resolved_effort"] == "xhigh"
    # Handler must pass wire["resolved_effort"] to CDP, not the sdk card clamp.
    cdp_wire = str(wire.get("resolved_effort") or "") or None
    assert cdp_wire == "max"


def test_reasoning_model_never_runs_the_mechanical_leg() -> None:
    model = resolve_desired_model("cursor/claude-opus-5", contract="implement")
    out, displaced = redirect_mechanical_executor(
        model,
        contract="implement",
        handoff_contract=resolve_handoff_contract("implement"),
    )
    assert displaced == "cursor/claude-opus-5"
    assert out["resolved_model_id"] == load_workflow_registry().workflows[
        MECHANICAL_WORKFLOW
    ].model
    # Opus-intrinsic knobs must not ride along onto the compose tier.
    assert out.get("model_knobs") == {}
    assert out["honored"] is False
    assert "compose tier implements" in str(out["notes"])


def test_reasoning_model_keeps_the_bind_leg() -> None:
    model = resolve_desired_model("cursor/claude-opus-5", contract="investigate")
    out, displaced = redirect_mechanical_executor(
        model,
        contract="investigate",
        handoff_contract=resolve_handoff_contract("investigate"),
    )
    assert displaced is None
    assert out["resolved_model_id"] == "cursor/claude-opus-5"


@pytest.mark.parametrize("requested", ["composer-2.5", "cursor/grok-4.6", "auto"])
def test_roaming_tier_runs_mechanical_work_untouched(requested: str) -> None:
    model = resolve_desired_model(requested, contract="implement")
    before = model["resolved_model_id"]
    out, displaced = redirect_mechanical_executor(
        model,
        contract="implement",
        handoff_contract=resolve_handoff_contract("implement"),
    )
    assert displaced is None
    assert out["resolved_model_id"] == before


@pytest.mark.asyncio
async def test_premium_override_no_longer_waives_empty_scope(
    _capture_events: list[tuple[str, dict[str, object]]],
) -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value="active"),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            AsyncMock(return_value=[]),
        )
        gate_out = await blocking_admit_gate(
            _job("cursor/claude-opus-5"),
            client=client,
            queue=MagicMock(),
        )
        blocked = gate_out.blocked

    assert blocked is not None
    assert blocked["terminal_status"] == "status:blocked"
    assert "roaming tier" in blocked["summary"]
    signals = [sig for sig, _ in _capture_events]
    assert "frontier.sdk.auto.empty_directive_scope_blocked" in signals
    assert "frontier.sdk.auto.empty_directive_scope_waived" not in signals


@pytest.mark.asyncio
async def test_roaming_override_still_waives_empty_scope(
    _capture_events: list[tuple[str, dict[str, object]]],
) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
            AsyncMock(return_value="active"),
        )
        mp.setattr(
            "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
            AsyncMock(return_value=[]),
        )
        result = await blocking_admit_gate(
            _job("composer-2.5"),
            client=AsyncMock(),
            queue=MagicMock(),
        )

    assert result.blocked is None
    assert any(
        sig == "frontier.sdk.auto.empty_directive_scope_waived"
        for sig, _ in _capture_events
    )
