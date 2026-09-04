"""Tests for attended-conductor resurface preamble in cursor-sdk packet assembly."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_packet import (
    _CONDUCTOR_HOP_TEMPLATE,
    extract_summon_mode_from_packet,
    resolve_prompt_preamble,
)

_ATTENDED_CONDUCTOR_PACKET = """---
packet_kind: conductor
contract: light-bounded
lane: B
---
<scope>
Conductor session. summon_mode: attended.
summoning_thread_id: 9638.
</scope>

<invariants>
Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows.
</invariants>
"""

_CONFER_CONDUCTOR_PACKET = """---
packet_kind: conductor
contract: light-bounded
lane: B
---
<scope>
Conductor session. summon_mode: confer_and_finish.
</scope>

<invariants>
Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows.
</invariants>
"""


def test_extract_summon_mode_attended() -> None:
    assert extract_summon_mode_from_packet("summon_mode: attended\n") == "attended"


def test_extract_summon_mode_hyphenated_confer() -> None:
    assert (
        extract_summon_mode_from_packet("summon_mode: confer-and-finish\n")
        == "confer_and_finish"
    )


def test_extract_summon_mode_absent() -> None:
    assert extract_summon_mode_from_packet("no summon mode here") is None


def test_attended_conductor_preamble_includes_resurface_block() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-9642",
        dispatch_id="5ee138e1094b-992cebb5",
        existing_text=_ATTENDED_CONDUCTOR_PACKET,
        has_packet_path=True,
    )
    assert "CONDUCTOR ATTENDED RESURFACE" in preamble
    assert "SCORE_RESURFACE" in preamble
    assert "summoning bus thread 9638" in preamble
    assert "never this leftover worker thread" in preamble
    assert "summoning lead" in preamble
    assert "liaison IDE" in preamble
    assert "not a page" in preamble
    assert "CONDUCTOR AWAY SCORE-RATIFY" not in preamble


def test_confer_conductor_preamble_includes_away_score_ratify() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-9642",
        dispatch_id="5ee138e1094b-992cebb5",
        existing_text=_CONFER_CONDUCTOR_PACKET,
        has_packet_path=True,
    )
    assert "CONDUCTOR ATTENDED RESURFACE" not in preamble
    assert "CONDUCTOR AWAY SCORE-RATIFY" in preamble
    assert "do-not-fight" in preamble


def test_absent_summon_mode_preamble_includes_away_score_ratify() -> None:
    absent_packet = """---
packet_kind: conductor
contract: light-bounded
lane: B
---
<scope>Conductor session — no summon_mode.</scope>
<invariants>Use the conductor skill.</invariants>
"""
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-9642",
        dispatch_id="5ee138e1094b-992cebb5",
        existing_text=absent_packet,
        has_packet_path=True,
    )
    assert "CONDUCTOR AWAY SCORE-RATIFY" in preamble
    assert "CONDUCTOR ATTENDED RESURFACE" not in preamble


def test_conductor_hop_template_pre_land_g6_before_g7_done() -> None:
    assert "G7 landed => DONE" in _CONDUCTOR_HOP_TEMPLATE
    assert "G6 landed => DONE" not in _CONDUCTOR_HOP_TEMPLATE
    assert "G6 review harvest unread" in _CONDUCTOR_HOP_TEMPLATE
