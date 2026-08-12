"""Falsifier: the autonomous lane must not commission unbounded premium work.

Auto POSTs the cursor-sdk worker directly, so Stargate's ``sdk_cost_risk`` guard
never sees these binds. Three bounds stand in for it — an executor bound (a
reasoning model never runs the mechanical leg), a scope bound (a ``contract:``
override stops waiving the empty-scope refusal outside the roaming tier), and an
effort ceiling (``xhigh``/``max`` need a standing trigger the unattended lane does
not have). These tests pin all three, and pin that the roaming tier is untouched
so the default mechanical path keeps its latitude.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_gates import blocking_admit_gate
from services.git_integration_worker.cursor_auto.dispatch_bounds import (
    AUTONOMOUS_EFFORT_CEILING,
    MECHANICAL_EXECUTOR_MODEL_ID,
    clamp_effort_to_autonomous_ceiling,
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
    ["composer-2.5", "cursor/composer-2.5", "grok-4.5", "cursor/grok-4.5"],
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


@pytest.mark.parametrize("requested", ["xhigh", "max"])
def test_premium_effort_clamps_to_ceiling(requested: str) -> None:
    out = clamp_effort_to_autonomous_ceiling(
        "cursor/claude-opus-5", _effort(requested)
    )
    assert out["resolved_effort"] == AUTONOMOUS_EFFORT_CEILING
    assert out["clamped"] is True
    assert requested in str(out["notes"])


@pytest.mark.parametrize("requested", ["low", "medium", "high"])
def test_effort_at_or_below_ceiling_is_identity(requested: str) -> None:
    payload = _effort(requested)
    assert clamp_effort_to_autonomous_ceiling("cursor/claude-opus-5", payload) is payload


@pytest.mark.parametrize("requested", ["xhigh", "max"])
def test_roaming_tier_keeps_full_effort_range(requested: str) -> None:
    payload = _effort(requested)
    assert clamp_effort_to_autonomous_ceiling("cursor/grok-4.5", payload) is payload


def test_sdk_clamp_does_not_define_cdp_wire_effort() -> None:
    """Document the split: sdk ceiling clamps; CDP commission uses unclamped wire."""
    from services.git_integration_worker.cursor_auto.wire_map import (
        resolve_desired_effort,
    )

    wire = resolve_desired_effort("xhigh")
    clamped = clamp_effort_to_autonomous_ceiling("cursor/claude-opus-5", wire)
    assert wire["resolved_effort"] == "xhigh"
    assert clamped["resolved_effort"] == AUTONOMOUS_EFFORT_CEILING
    # Handler must pass wire["resolved_effort"] to CDP, not clamped.
    cdp_wire = str(wire.get("resolved_effort") or "") or None
    assert cdp_wire == "xhigh"


def test_reasoning_model_never_runs_the_mechanical_leg() -> None:
    model = resolve_desired_model("cursor/claude-opus-5", contract="implement")
    out, displaced = redirect_mechanical_executor(
        model,
        contract="implement",
        handoff_contract=resolve_handoff_contract("implement"),
    )
    assert displaced == "cursor/claude-opus-5"
    assert out["resolved_model_id"] == MECHANICAL_EXECUTOR_MODEL_ID
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


@pytest.mark.parametrize("requested", ["composer-2.5", "cursor/grok-4.5", "auto"])
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
