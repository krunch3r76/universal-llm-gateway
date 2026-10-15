"""Probe: ``team_dispatch(job=freeform)`` is freeform on the GIW admit path."""

from __future__ import annotations

import pytest
from reasoning_posture_contracts import contract_is_freeform

from services.git_integration_worker.cursor_sdk_packet import (
    _BARE_REFUSAL_LINE,
    _FAILURE_PATHS_BLOCK,
    resolve_prompt_preamble,
)


def _assert_bare_lane_b_floor(preamble: str) -> None:
    """Binding floor. Fails when the worktree, failure-paths, or refusal line is gone."""
    assert "LANE-B WORKTREE" in preamble
    assert "/tmp/wt-bare" in preamble
    assert _FAILURE_PATHS_BLOCK in preamble
    assert _BARE_REFUSAL_LINE in preamble
    assert "forward_only:" in preamble
    assert "in-flight execution" in preamble


def _mutation_pair(preamble: str) -> None:
    """Same assertion passes with the floor and fails when those markers are removed."""
    _assert_bare_lane_b_floor(preamble)
    stripped = preamble
    for marker in (
        "LANE-B WORKTREE",
        _FAILURE_PATHS_BLOCK,
        _BARE_REFUSAL_LINE,
    ):
        stripped = stripped.replace(marker, "")
    with pytest.raises(AssertionError):
        _assert_bare_lane_b_floor(stripped)


def test_contract_is_freeform_only_freeform_job() -> None:
    assert contract_is_freeform("freeform") is True
    assert contract_is_freeform("none") is False
    assert contract_is_freeform("consult") is False
    assert contract_is_freeform("pure-mechanical") is False
    assert contract_is_freeform("implement") is False


def test_none_freeform_skips_harness_preamble_stack() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="freeform",
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
        handoff_contract="freeform",
        prompt_preamble=None,
        inferred_contract=None,
        skills=["architecture-invariants"],
    )
    assert preamble.startswith("/reasoning-posture")
    assert "Use the `architecture-invariants` skill" in preamble
    assert "DURABLE DELIVERABLE ROUTING" not in preamble


def test_confer_still_gets_judgment_stack() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="confer",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "DURABLE DELIVERABLE ROUTING" in preamble
    assert "Use the `reasoning-posture` skill" in preamble
    assert "Use the `hypothesize-simulate` skill" in preamble


def test_bare_contract_emits_lane_b_floor() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="bare",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-bare",
        lane_worktree="/tmp/wt-bare",
        continuity_root_thread_id="16416",
    )
    _mutation_pair(preamble)
    assert "/reasoning-posture" not in preamble
    assert "DURABLE DELIVERABLE ROUTING" not in preamble
    assert "LANE-B BRANCH CONTRACT" not in preamble
    assert "ulg-for-llms" not in preamble


def test_bare_contract_honors_opt_in_skills_and_keeps_floor() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="bare",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-bare",
        lane_worktree="/tmp/wt-bare",
        skills=["cortex-orientation"],
    )
    _mutation_pair(preamble)
    assert "Use the `cortex-orientation` skill" in preamble
    assert "/reasoning-posture" not in preamble
    assert "DURABLE DELIVERABLE ROUTING" not in preamble
    assert "LANE-B BRANCH CONTRACT" not in preamble
