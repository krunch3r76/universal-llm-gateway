"""Packet rendering tests (§8)."""

from __future__ import annotations

import pytest
from mechanical_land.packets import (
    HARD_BANS_BLOCK,
    check_review_prompt,
    render_fix_packet,
    render_review_packet,
    review_request_body,
)

pytestmark = pytest.mark.offline

HEX40 = "a" * 40


def test_hard_bans_fixture_byte_identical() -> None:
    fixture = """## Hard bans (a:40262, git-posture § Forward only)
- Never run any form of `git stash` (stash, stash -u, stash pop, stash apply, stash drop).
- Never run `git checkout`, `git switch`, `git reset`, `git clean`, or `git restore` against the shared checkout /mnt/torus/projects/universal-llm-gateway. Your worktree is the only tree you touch.
- Never `git push --force`, never rewrite history, never cherry-pick onto master. Never push at all.
- Do not land. Do not restart services. Do not post to agent-bus except your CLOSEOUT (or, for a reviewer, your VERDICT).
- Change only the files named in files_expected. Anything else you think should change goes in your CLOSEOUT under `proposals:`.
"""
    assert HARD_BANS_BLOCK == fixture


def test_gate_fix_wraps_gate_output_and_bans_last() -> None:
    pkt = render_fix_packet(
        run_id="ml-x",
        step_id="fix",
        attempt=1,
        reason="gate_red",
        gate_output="ruff: N806",
    )
    assert "<gate_output>\nruff: N806\n</gate_output>" in pkt
    assert pkt.endswith(HARD_BANS_BLOCK)


def test_review_fix_wraps_blockers() -> None:
    pkt = render_fix_packet(
        run_id="ml-x",
        step_id="fix",
        attempt=2,
        reason="review_request_changes",
        review_blockers="B1: missing test",
    )
    assert "<review_blockers>\nB1: missing test\n</review_blockers>" in pkt
    assert pkt.endswith(HARD_BANS_BLOCK)


def test_review_packet_range_head_verdict_grammar_fixes_on_re_review() -> None:
    pkt = render_review_packet(
        run_id="ml-x",
        attempt=2,
        range_spec=f"{HEX40}..{HEX40}",
        head=HEX40,
        fixes=HEX40,
    )
    assert f"range={HEX40}..{HEX40}" in pkt
    assert f"head={HEX40}" in pkt
    assert f"fixes={HEX40}" in pkt
    assert "VERDICT:" in pkt
    assert pkt.endswith(HARD_BANS_BLOCK)


def test_route_a_and_b_same_packet_bytes() -> None:
    pkt = render_review_packet(
        run_id="ml-x",
        attempt=1,
        range_spec=f"{HEX40}..{HEX40}",
        head=HEX40,
    )
    assert review_request_body(pkt) == pkt
    assert check_review_prompt(pkt) == pkt
