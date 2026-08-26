"""Tests for attended-conductor resurface preamble in cursor-sdk packet assembly."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_packet import (
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
    assert "resurface the scoreboard tip" in preamble
    assert "no pager" in preamble


def test_confer_conductor_preamble_omits_resurface_block() -> None:
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
