"""Tests for resume bundle mission block (FIX-16..18)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.resume_fence_mission import (
    build_mission_block,
    mission_marker_preview,
    sketchboard_uri,
)

pytestmark = pytest.mark.offline

_TIP = """
## Residue (authored — cap ~800 chars)
## Anchor
Window: transcript_id=d556c84f-524e-4e57-b38c-6fdc3eafe8fb · turns@cp=22

## Highlight
Structural resume PASS.

## Settled
FIX-7..14 landed.

## Next
FIX-18 shrink mission block.

Sketchboard: cortex://notes/system/threads/10223-resume-fence-sketchboard.md
"""


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="continuity", tags=["role:root"])
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="house",
        subject="INFO relay",
        body="TYPE: INFO",
        status="open",
    )
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="house",
        subject="CHECKPOINT",
        body=_TIP,
        status="open",
    )
    yield


def test_sketchboard_uri_from_tip() -> None:
    uri = sketchboard_uri("10223", _TIP)
    assert uri.endswith("10223-resume-fence-sketchboard.md")


def test_build_mission_block_includes_residue_and_window(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={
            "checkpoint_highlight": "Structural resume PASS.",
            "consolidate_summary_row": "row",
            "summary_row_source": "tip_checkpoint_residue",
            "summary_row_as_of_turn": 2,
        },
        pools_row=None,
        open_line=None,
        fence_id="rf-test1234",
    )
    assert mission["fence_id"] == "rf-test1234"
    assert mission["window_anchor"]["transcript_id"].startswith("d556c84f")
    assert mission["window_anchor"].keys() == {"transcript_id", "turns_at_cp"}
    assert mission["lifecycle"]["clone_mode"] == "B"
    assert not any("scope=window" in step for step in mission["handoff"]["steps"])
    assert mission["lifecycle"]["release"] == "pour_terminal"
    assert "FIX-18" in (mission.get("residue") or "")
    assert mission["handoff"]["todo"] == "todo:continuity-resume-fence"
    assert mission["bus_tail"] == ["10223#1"]
    assert mission["house_unread"] == [
        {
            "turn": 1,
            "from": "cursor",
            "to": "house",
            "subject": "INFO relay",
            "body": "TYPE: INFO",
        }
    ]
    assert any("house_unread" in step for step in mission["handoff"]["steps"])


def test_handoff_steps_thread_slug_and_out_of_scope(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body="plain tip without sketchboard uri",
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-slug",
        thread_slug="liaison-autonomous-night",
    )
    steps = mission["handoff"]["steps"]
    assert "rename_chat → `10223 liaison-autonomous-night`" in steps
    assert not any("read sketchboard" in step for step in steps)
    assert mission["handoff"]["out_of_scope"] == [
        "parallel WIP outside this root's scoreboard",
        "new implement until oriented",
    ]


def test_handoff_sketchboard_step_only_when_named_in_tip(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-sketch",
        thread_slug="continuity",
    )
    steps = mission["handoff"]["steps"]
    assert any(
        "read sketchboard cortex://notes/system/threads/10223-resume-fence-sketchboard.md"
        in step
        for step in steps
    )


def test_handoff_omits_rename_when_slug_missing(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-noslug",
        thread_slug=None,
    )
    assert not any("rename_chat" in step for step in mission["handoff"]["steps"])


def test_house_unread_includes_same_from_note(bus_db) -> None:
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="NOTE a:38216 specimen",
        body="TYPE: NOTE\nfrom: 15266",
        status="open",
    )
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-note",
    )
    notes = [row for row in mission["house_unread"] if row["turn"] == 3]
    assert notes == [
        {
            "turn": 3,
            "from": "cursor",
            "to": "web-anthropic",
            "subject": "NOTE a:38216 specimen",
            "body": "TYPE: NOTE\nfrom: 15266",
        }
    ]


def test_mission_marker_preview_is_subset(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={"checkpoint_highlight": "hi"},
        pools_row=None,
        open_line="one line",
        fence_id="rf-abcd",
    )
    preview = mission_marker_preview(mission)
    assert preview["highlight"] == "hi"
    assert preview["clone_mode"] == "B"
    assert preview["skills_to_use"] == []
    assert "handoff" not in preview
    assert "residue" not in preview


_SKILLS_CARD = """
## Skills
- `outbound-voice-spec`
- `prose-discipline`

## Stance
Use the `ulg-for-llms` skill.
"""


def test_mission_skills_to_use_from_card_and_step_order(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=_SKILLS_CARD,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-skills",
        thread_slug="continuity",
    )
    assert mission["skills_to_use"] == [
        {
            "slug": "outbound-voice-spec",
            "use_line": "Use the `outbound-voice-spec` skill",
        },
        {
            "slug": "prose-discipline",
            "use_line": "Use the `prose-discipline` skill",
        },
    ]
    steps = mission["handoff"]["steps"]
    assert steps[0] == "continuity(op=resume) was first hop"
    assert steps[1] == (
        "Use each mission.skills_to_use slug now — before orientation "
        "and before any act the slug governs"
    )
    assert "rename_chat → `10223 continuity`" in steps
    preview = mission_marker_preview(mission)
    assert preview["skills_to_use"] == mission["skills_to_use"]


def test_standing_rules_resolve_assertions(bus_db, monkeypatch: pytest.MonkeyPatch) -> None:
    long_claim = "no rice " * 80

    def _fake(assertion_id: int) -> dict:
        if assertion_id == 31294:
            return {
                "id": "a:31294",
                "status": "current",
                "claim": long_claim,
                "claim_truncated": False,
            }
        if assertion_id == 1:
            return {
                "id": "a:1",
                "status": "superseded",
                "claim": "old claim",
                "claim_truncated": False,
            }
        if assertion_id == 2:
            return {
                "id": "a:2",
                "status": "retracted",
                "claim": "withdrawn",
                "claim_truncated": False,
            }
        return {
            "id": f"a:{assertion_id}",
            "status": "unresolved",
            "claim": "",
            "claim_truncated": False,
        }

    monkeypatch.setattr(
        "agent_bus_store.resume_fence_mission.lookup_assertion",
        _fake,
    )
    row = (
        "- Diet, loaded before any meal: `person:kaywan-mansubi` "
        "a:31294 · a:1 · a:2. No rice."
    )
    card = f"## Rules\n{row}\n\n## Skills\n- `ulg-for-llms`\n"
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=card,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-rules",
    )
    assert mission["standing_rules"][0]["text"] == row
    by_id = {item["id"]: item for item in mission["standing_rules"][0]["assertions"]}
    assert by_id["a:31294"]["status"] == "current"
    assert by_id["a:31294"]["claim_truncated"] is True
    assert len(by_id["a:31294"]["claim"]) <= 240
    assert by_id["a:1"]["status"] == "superseded"
    assert by_id["a:2"]["status"] == "retracted"
    steps = mission["handoff"]["steps"]
    assert steps[0].startswith("Load mission.standing_rules")
    assert steps[1] == "continuity(op=resume) was first hop"


def test_missing_rules_section_degrades(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text="## Stance\nUse the `ulg-for-llms` skill.\n",
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-norules",
    )
    assert mission["standing_rules"] == []
    assert not any("standing_rules" in step for step in mission["handoff"]["steps"])


def test_missing_card_degrades(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text=None,
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-nocard",
    )
    assert mission["standing_rules"] == []


def test_mission_no_skills_section_empty_list_no_step(bus_db) -> None:
    mission = build_mission_block(
        thread_id="10223",
        tip_body=_TIP,
        tip_turn=2,
        supersedes_turn=None,
        card_text="## Stance\nUse the `ulg-for-llms` skill.\n",
        envelope={},
        pools_row=None,
        open_line=None,
        fence_id="rf-noskills",
    )
    assert mission["skills_to_use"] == []
    assert not any("skills_to_use" in step for step in mission["handoff"]["steps"])
