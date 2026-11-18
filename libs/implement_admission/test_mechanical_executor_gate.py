"""Unit tests for mechanical_executor_gate."""

from __future__ import annotations

import pytest

from implement_admission.mechanical_executor_gate import (
    IMPLEMENT_COMPOSER_WAIVER_MARKER,
    effective_mechanical_contract,
    evaluate_mechanical_executor_gate,
    implement_composer_waiver_valid,
    mechanical_executor_gate_applies,
    mechanical_implement_bare_id,
    mechanical_implement_model,
    primary_mechanical_contract_signal,
    refuse_stargate_mechanical_executor,
)


def test_mechanical_implement_model_from_registry() -> None:
    assert mechanical_implement_model() == "cursor/composer-2.5"
    assert mechanical_implement_bare_id() == "composer-2.5"


@pytest.mark.parametrize(
    ("handoff", "operator", "inferred", "expected"),
    [
        ("pure-mechanical", "implement", None, "implement"),
        ("implement", None, None, "implement"),
        (None, None, "implement", "implement"),
        ("confer", None, None, "confer"),
    ],
)
def test_effective_mechanical_contract(
    handoff: str | None, operator: str | None, inferred: str | None, expected: str
) -> None:
    assert (
        effective_mechanical_contract(
            handoff_contract=handoff,
            operator_contract=operator,
            inferred_contract=inferred,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("handoff", "operator", "inferred", "applies"),
    [
        ("implement", "confer", None, True),
        ("implement", None, None, True),
        ("pure-mechanical", "implement", None, True),
        ("confer", None, None, False),
        (None, "confer", None, False),
        (None, None, "implement", True),
    ],
)
def test_mechanical_executor_gate_applies_union(
    handoff: str | None,
    operator: str | None,
    inferred: str | None,
    applies: bool,
) -> None:
    assert (
        mechanical_executor_gate_applies(
            handoff_contract=handoff,
            operator_contract=operator,
            inferred_contract=inferred,
        )
        is applies
    )


def test_primary_mechanical_signal_prefers_handoff_over_operator_confer() -> None:
    assert (
        primary_mechanical_contract_signal(
            handoff_contract="implement",
            operator_contract="confer",
            inferred_contract=None,
        )
        == "implement"
    )


def test_implement_handoff_operator_confer_still_refuses_grok() -> None:
    applies = mechanical_executor_gate_applies(
        handoff_contract="implement",
        operator_contract="confer",
        inferred_contract=None,
    )
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=applies,
        effective_contract=primary_mechanical_contract_signal(
            handoff_contract="implement",
            operator_contract="confer",
            inferred_contract=None,
        ),
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
    )
    assert not verdict.allowed
    assert verdict.validation_stage == "contract_model"


def test_refuse_grok_on_implement() -> None:
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=True,
        effective_contract="implement",
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
    )
    assert not verdict.allowed
    assert verdict.validation_stage == "contract_model"


def test_allow_composer_on_implement() -> None:
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=True,
        effective_contract="implement",
        wire_model="cursor/composer-2.5",
        resolved_bare_model="composer-2.5",
    )
    assert verdict.allowed


def test_resume_parent_child_match_bypasses_gate() -> None:
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=True,
        effective_contract="implement",
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
        resume_of="parent-disp",
        parent_resolved_model="cursor/grok-4.7",
    )
    assert verdict.allowed


def test_resume_mismatch_still_refuses() -> None:
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=True,
        effective_contract="implement",
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
        resume_of="parent-disp",
        parent_resolved_model="cursor/composer-2.5",
    )
    assert not verdict.allowed


def test_waiver_trio_allows() -> None:
    assert implement_composer_waiver_valid(
        ban_override=True,
        ban_override_reason=f"audit {IMPLEMENT_COMPOSER_WAIVER_MARKER}",
        model_ban_override_event_id="evt-1",
    )
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=True,
        effective_contract="implement",
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
        ban_override=True,
        ban_override_reason=f"ops {IMPLEMENT_COMPOSER_WAIVER_MARKER} note",
        model_ban_override_event_id="evt-waiver",
    )
    assert verdict.allowed


def test_partial_waiver_refuses() -> None:
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=True,
        effective_contract="implement",
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
        ban_override=True,
        ban_override_reason="missing marker",
        model_ban_override_event_id="evt-waiver",
    )
    assert not verdict.allowed


def test_confer_grok_not_gated() -> None:
    verdict = evaluate_mechanical_executor_gate(
        gate_applies=False,
        effective_contract="confer",
        wire_model="cursor/grok-4.7",
        resolved_bare_model="grok-4.7",
    )
    assert verdict.allowed


def test_stargate_refuse_explicit_grok_implement() -> None:
    with pytest.raises(ValueError, match="mechanical_executor_refused"):
        refuse_stargate_mechanical_executor(
            job="implement",
            contract=None,
            requested_model="cursor/grok-4.7",
            resolved_model="cursor/grok-4.7",
        )


def test_stargate_allow_explicit_grok_confer() -> None:
    refuse_stargate_mechanical_executor(
        job="confer",
        contract=None,
        requested_model="cursor/grok-4.7",
        resolved_model="cursor/grok-4.7",
    )