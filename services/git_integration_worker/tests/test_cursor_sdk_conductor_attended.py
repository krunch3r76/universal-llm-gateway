"""Tests for attended-conductor resurface preamble in cursor-sdk packet assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from implement_admission.conductor_critic_render import g3_g5_score_ratify_clause
from implement_admission.impresario_policy import resolve_roles

from services.git_integration_worker.cursor_sdk_packet import (
    _CONDUCTOR_HOP_TEMPLATE,
    _conductor_width_roles_for_summoning_thread,
    extract_summon_mode_from_packet,
    resolve_prompt_preamble,
)

_ATTENDED_CONDUCTOR_PACKET = """---

contract: conductor
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

contract: conductor
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
        handoff_contract="conductor",
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
    critic = resolve_roles().critic
    assert critic.model in preamble
    assert critic.model.split("/", 1)[-1] in preamble or critic.model in preamble
    assert "summoning bus thread 9638" in preamble
    assert "never this leftover worker thread" in preamble
    assert "required attended G5 fold witness (a:37198)" in preamble
    assert "not implement completeness" in preamble
    assert "harvest alone does not close G5" in preamble
    assert "not a stop" in preamble
    assert "OPERATOR_GATE" in preamble
    assert "CONDUCTOR AWAY SCORE-RATIFY" not in preamble


def test_confer_conductor_preamble_includes_away_score_ratify() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="conductor",
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

contract: conductor
lane: B
---
<scope>Conductor session — no summon_mode.</scope>
<invariants>Use the conductor skill.</invariants>
"""
    preamble = resolve_prompt_preamble(
        handoff_contract="conductor",
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


@pytest.fixture
def house_watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import bus_watch.fable_lock as fl

    monkeypatch.setattr(fl, "WATCH_DIR", tmp_path)
    return tmp_path


def _write_house_tick(
    watch_dir: Path,
    root_id: str,
    *,
    critic_model: str | None = None,
    effort: str = "high",
) -> None:
    policy: dict[str, object] = {"gear": "1-fable-mvp"}
    if critic_model:
        policy["critic_model"] = critic_model
        policy["critic_model_knobs"] = {"effort": effort}
    tick = {"policy": policy}
    (watch_dir / f"liaison-{root_id}.tick.json").write_text(
        json.dumps(tick), encoding="utf-8"
    )


_CONDUCTOR_WITH_SUMMONING = """---

contract: conductor
lane: B
---
<scope>
Conductor session. summon_mode: confer_and_finish.
summoning_thread_id: 21558.
</scope>

<invariants>
Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows.
</invariants>
"""


def test_conductor_preamble_names_house_critic_in_all_three_blocks(
    house_watch_dir: Path,
) -> None:
    house_model = "cursor/grok-4.7"
    _write_house_tick(house_watch_dir, "21558", critic_model=house_model, effort="high")
    house_clause = g3_g5_score_ratify_clause(
        resolve_roles(critic_model=house_model, critic_effort="high")
    )
    global_clause = g3_g5_score_ratify_clause()
    common = dict(
        handoff_contract="conductor",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-21558",
        dispatch_id="5ee138e1094b-992cebb5",
        has_packet_path=True,
        thread_id="worker-thread-99",
        hop_seq=2,
    )
    attended_packet = _ATTENDED_CONDUCTOR_PACKET.replace(
        "summoning_thread_id: 9638.", "summoning_thread_id: 21558."
    )
    _write_house_tick(house_watch_dir, "21558", critic_model=house_model, effort="high")
    attended = resolve_prompt_preamble(existing_text=attended_packet, **common)
    assert house_clause in attended
    assert "CONDUCTOR ATTENDED RESURFACE" in attended
    assert global_clause not in attended

    away = resolve_prompt_preamble(existing_text=_CONDUCTOR_WITH_SUMMONING, **common)
    assert house_clause in away
    assert "CONDUCTOR AWAY SCORE-RATIFY" in away
    assert global_clause not in away
    assert "CONDUCTOR HOP" in away
    assert away.count(house_clause) == 2


def test_conductor_preamble_without_house_critic_matches_global_clause(
    house_watch_dir: Path,
) -> None:
    baseline = resolve_prompt_preamble(
        handoff_contract="conductor",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-21558",
        dispatch_id="5ee138e1094b-992cebb5",
        existing_text=_CONFER_CONDUCTOR_PACKET,
        has_packet_path=True,
        thread_id="worker-thread-99",
        hop_seq=1,
    )
    _write_house_tick(house_watch_dir, "99999", critic_model="cursor/grok-4.7")
    assert baseline == resolve_prompt_preamble(
        handoff_contract="conductor",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-21558",
        dispatch_id="5ee138e1094b-992cebb5",
        existing_text=_CONFER_CONDUCTOR_PACKET,
        has_packet_path=True,
        thread_id="worker-thread-99",
        hop_seq=1,
    )


def test_house_tick_read_failure_falls_back_without_raise(
    house_watch_dir: Path,
) -> None:
    (house_watch_dir / "liaison-21558.tick.json").write_text("{not json", encoding="utf-8")
    assert _conductor_width_roles_for_summoning_thread("21558") is None
