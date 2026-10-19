"""Turn-0 prompt preamble must not restate fixed invariant blocks."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_sdk_packet import (
    _FAILURE_PATHS_BLOCK,
    _LANE_B_WORKTREE_TEMPLATE,
    _TIER_C_LEAF_PREAMBLE,
    resolve_prompt_preamble,
)


@pytest.mark.offline
def test_turn0_skips_tier_leaf_when_prompt_preamble_carries_it() -> None:
    prompt_preamble = f"{_TIER_C_LEAF_PREAMBLE}\n\nCommission text."
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=prompt_preamble,
        inferred_contract="implement",
        lane="B",
        lane_worktree="/tmp/lane-worktree",
        delegation_tier="C",
        existing_text="packet body",
    )
    assert preamble.count(_TIER_C_LEAF_PREAMBLE) == 1


@pytest.mark.offline
def test_turn0_skips_failure_paths_when_in_prompt_preamble() -> None:
    prompt_preamble = f"Intro\n\n{_FAILURE_PATHS_BLOCK}"
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=prompt_preamble,
        inferred_contract="implement",
        lane="B",
        lane_worktree="/tmp/lane-worktree",
        delegation_tier="C",
        existing_text="",
    )
    assert preamble.count("Before you post.") == 1


@pytest.mark.offline
def test_conductor_hop_may_restate_worktree_block() -> None:
    lane_worktree = "/tmp/lane-worktree"
    prompt_preamble = _LANE_B_WORKTREE_TEMPLATE.format(lane_worktree=lane_worktree)
    preamble = resolve_prompt_preamble(
        handoff_contract="conductor",
        prompt_preamble=prompt_preamble,
        inferred_contract="conductor",
        lane="B",
        lane_worktree=lane_worktree,
        delegation_tier="C",
        dispatch_id="auto-d1",
        has_packet_path=True,
        existing_text="Use the conductor skill.\n",
        hop_seq=2,
        hop_from="auto-prev",
        thread_id="123",
    )
    assert preamble.count("LANE-B WORKTREE") >= 2
