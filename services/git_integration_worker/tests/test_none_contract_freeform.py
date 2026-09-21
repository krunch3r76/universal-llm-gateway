"""Probe: ``team_dispatch(contract=\"none\")`` is freeform on the GIW admit path."""

from __future__ import annotations

from reasoning_posture_contracts import contract_is_freeform

from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble


def test_contract_is_freeform_only_none() -> None:
    assert contract_is_freeform("none") is True
    assert contract_is_freeform("consult") is False
    assert contract_is_freeform("pure-mechanical") is False
    assert contract_is_freeform("implement") is False


def test_none_freeform_skips_harness_preamble_stack() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="none",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-probe",
        continuity_root_thread_id="11650",
    )
    assert preamble.startswith("/reasoning-posture")
    assert "Use the `reasoning-posture` skill" in preamble
    assert "DURABLE DELIVERABLE ROUTING" not in preamble
    assert "LANE-B BRANCH CONTRACT" not in preamble
    assert "ulg-for-llms" not in preamble


def test_none_freeform_honors_explicit_caller_skills() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="none",
        prompt_preamble=None,
        inferred_contract=None,
        skills=["architecture-invariants"],
    )
    assert preamble.startswith("/reasoning-posture")
    assert "Use the `architecture-invariants` skill" in preamble
    assert "DURABLE DELIVERABLE ROUTING" not in preamble


def test_consult_still_gets_judgment_stack() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="consult",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "DURABLE DELIVERABLE ROUTING" in preamble
    assert "Use the `reasoning-posture` skill" in preamble
    assert "Use the `hypothesize-simulate` skill" in preamble
