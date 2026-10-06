"""Parse unit tests (AC1 section parse, AC2 recipe, AC5 fixtures)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from maestro_induct.parse import (
    is_checkpoint_subject,
    is_consult_turn,
    next_checkpoint_page,
    parse_card,
    parse_checkpoint_residue,
    parse_consult_turn,
    parse_continuity_current,
    parse_journal_entries,
    parse_runbook_steps,
    parse_scoreboard_tip,
    pick_checkpoint_turn,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_card_headings_12286() -> None:
    body = (FIXTURES / "12286-card.md").read_text(encoding="utf-8")
    parsed = parse_card(body)
    titles = [h["title"] for h in parsed["headings"]]
    assert titles[0] == "Stance"
    assert titles[-1] == "House"
    assert len(titles) == 11


def test_pick_checkpoint_turn_newest() -> None:
    tip = json.loads((FIXTURES / "turns-12286-tip.json").read_text())["turns"]
    assert pick_checkpoint_turn(tip) == 35
    assert pick_checkpoint_turn(list(reversed(tip))) == 35
    assert pick_checkpoint_turn([{"turn_number": 1, "subject": "NOTE"}]) is None


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("CHECKPOINT 12286 b2a9c2b7", True),
        ("  checkpoint — leg 5", True),
        ("LANE CLOSEOUT agent-bus:15323 closed", False),
        ("", False),
    ],
)
def test_is_checkpoint_subject(subject: str, expected: bool) -> None:
    assert is_checkpoint_subject(subject) is expected


@pytest.mark.parametrize(
    "lowest,page,expected",
    [
        (2522, 1000, (1521, 1000)),
        (522, 1000, (0, 521)),
        (51, 1000, (0, 50)),
        (1, 1000, None),
    ],
)
def test_next_checkpoint_page(lowest: int, page: int, expected) -> None:
    assert next_checkpoint_page(lowest_seen=lowest, page_size=page) == expected


def test_parse_checkpoint_residue_12286_35() -> None:
    turn = json.loads((FIXTURES / "12286-cp-turn-35.json").read_text())
    out = parse_checkpoint_residue(turn["body"])
    assert out["anchor"].startswith("Objective: make web-anthropic a working maestro")
    assert out["state"].startswith("- Primary OPEN: A G7 land IN FLIGHT")
    assert len(out["steps"]) == 8
    assert out["next"].startswith("sweep lanes on wake")
    assert out["next_source"] == "handoff_bullet"


def test_parse_checkpoint_residue_12286_17() -> None:
    turn = json.loads((FIXTURES / "12286-cp-turn-17.json").read_text())
    out = parse_checkpoint_residue(turn["body"])
    assert out["anchor"].startswith("Objective: make web-anthropic a working ear")
    assert len(out["steps"]) == 7
    assert out["next"].startswith("harvest whichever CONSULT_PENDING")


def test_parse_checkpoint_residue_12286_138() -> None:
    turn = json.loads((FIXTURES / "12286-cp-turn-138.json").read_text())
    out = parse_checkpoint_residue(turn["body"])
    assert out["anchor"].startswith("Window: transcript_id=")
    assert out["state"] is None
    assert out["steps"] == []


def test_parse_continuity_current_12286() -> None:
    text = (FIXTURES / "12286-continuity.md").read_text(encoding="utf-8")
    out = parse_continuity_current(text)
    assert set(out) == {"settled", "live", "latest_leg", "next_pickup"}
    assert out["settled"].startswith("- Root 12286 minted")
    assert "Leg 10" in out["latest_leg"]["title"]
    assert len(out["next_pickup"]) == 10


def test_parse_continuity_current_missing() -> None:
    assert "error" in parse_continuity_current("# card\n")


def test_parse_runbook_steps_12286() -> None:
    text = (FIXTURES / "12286-house-runbook.md").read_text(encoding="utf-8")
    steps = parse_runbook_steps(text)
    assert len(steps) == 7
    assert steps[0].startswith("1.")


def test_consult_harvest_recipe_12291_12() -> None:
    turn = json.loads((FIXTURES / "12291-turn-12-consult.json").read_text())
    out = parse_consult_turn(turn)
    assert out["thread"] == "12291"
    assert out["turn"] == 12
    assert out["subject_prefix"] == "HARVEST — G6"
    assert out["send"]["from_agent"] == "web-anthropic"


def test_consult_turn_12359_5_subject_gate_wins() -> None:
    turn = json.loads((FIXTURES / "12359-turn-5-consult.json").read_text())
    assert is_consult_turn(turn)
    out = parse_consult_turn(turn)
    assert out["subject_prefix"] == "HARVEST — G2"


def test_is_consult_body_leg_needs_body() -> None:
    assert is_consult_turn({"subject": "lane status", "body": "TYPE: CONSULT_PENDING\n"})
    assert not is_consult_turn({"subject": "lane status", "body": None})
    assert is_consult_turn({"subject": "CONSULT_PENDING — G6", "body": None})


def test_journal_never_mid_entry() -> None:
    text = (FIXTURES / "12286-journal.md").read_text(encoding="utf-8")
    entries = parse_journal_entries(text, limit=2)
    assert entries
    full = parse_journal_entries(text, limit=99)
    for e in entries:
        assert any(e["title"] == f["title"] and e["body"] == f["body"] for f in full)


def test_ac5_fixtures_present_and_sha() -> None:
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    for name in (
        "12286-card.md",
        "12286-cp-turn-35.json",
        "12286-journal.md",
        "12291-turn-12-consult.json",
        "12286-continuity.md",
        "12286-cp-turn-17.json",
        "12286-cp-turn-138.json",
    ):
        assert name in manifest
        digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert digest == manifest[name]["read_sha256"]


def test_scoreboard_tip_variants() -> None:
    body = (FIXTURES / "maestro-induct-pipeline-scoreboard.md").read_text(encoding="utf-8")
    out = parse_scoreboard_tip(body, slug="maestro-induct-pipeline", discovered_by="graph")
    assert out["entry_gate"] in {"G3", "G5"}  # frozen fixture may trail live scoreboard gate
    assert out["next_admit"] is None
