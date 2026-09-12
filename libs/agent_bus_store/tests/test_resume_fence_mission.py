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
    assert mission["lifecycle"]["clone_mode"] == "B"
    assert mission["lifecycle"]["release"] == "pour_terminal"
    assert "FIX-18" in (mission.get("residue") or "")
    assert mission["handoff"]["todo"] == "todo:continuity-resume-fence"
    assert mission["bus_tail"] == ["10223#1"]


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
    assert "handoff" not in preview
    assert "residue" not in preview
