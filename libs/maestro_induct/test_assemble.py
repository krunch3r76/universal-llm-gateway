"""Assemble and cap tests."""

from __future__ import annotations

import json
from pathlib import Path

from maestro_induct.assemble import (
    ALLOWED_LIVE_ERROR_KINDS,
    apply_size_caps,
    assemble_packet,
    check_packet_sections,
    compute_first_act,
    dumps_packet,
    packet_nbytes,
)
from maestro_induct.parse import (
    parse_checkpoint_residue,
    parse_continuity_current,
    parse_journal_entries,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED_RULE = (
    "`MAESTRO 12286` in context ⇒ the seat calls the pipeline first, before any other tool."
)


def _minimal_sections(**overrides):
    base = {
        "resolve": {
            "root": "12286",
            "started_at": "2026-10-06T00:00:00Z",
            "skipped": [],
            "errors": [],
        },
        "fetch_house": {"headings": [], "card_sha256": "abc", "runbook_uris": []},
        "fetch_checkpoint": {"turn": 138},
        "fetch_continuity": parse_continuity_current(
            (FIXTURES / "12286-continuity.md").read_text(encoding="utf-8")
        ),
        "fetch_journal": {"entries": []},
        "fetch_runbook": {"steps": ["1. Boot:"]},
        "fetch_scores": {"scores": [{"slug": "x", "entry_gate": "G1"}]},
        "enumerate_lanes": {"lane_ids": ["12286", "12291"]},
        "fetch_lanes": {"lanes": []},
        "fetch_consults": {"open_consults": [], "open_consults_outside_house": [], "truncated_meta": {}},
    }
    base.update(overrides)
    return base


def test_build_reinduct_trigger_contract() -> None:
    packet = assemble_packet(sections=_minimal_sections(), options={"root": "12286"})
    assert packet["reinduct"]["trigger"]["rule"] == EXPECTED_RULE
    assert packet["reinduct"]["call"]["pipeline_id"] == "maestro-induct"
    assert packet["reinduct"]["call"]["options"]["root"] == "12286"


def test_compute_first_act_consult_branch() -> None:
    consult = {"thread": "12286", "turn": 900, "subject_prefix": "HARVEST — G4"}
    item = {"thread": "12291", "turn": 12, "subject_prefix": "HARVEST — G6"}
    act, src = compute_first_act(
        open_consults=[consult, item],
        continuity={},
        checkpoint={},
    )
    assert act.startswith("HARVEST 12291#12")
    assert src == "consult"


def test_compute_first_act_continuity_branch() -> None:
    cont = parse_continuity_current((FIXTURES / "12286-continuity.md").read_text(encoding="utf-8"))
    act, src = compute_first_act(open_consults=[], continuity=cont, checkpoint={})
    assert act.startswith("**Seat state")
    assert src == "continuity"


def test_compute_first_act_cp_step_branch() -> None:
    turn = json.loads((FIXTURES / "12286-cp-turn-35.json").read_text())
    cp = parse_checkpoint_residue(turn["body"])
    cp["turn"] = 35
    act, src = compute_first_act(open_consults=[], continuity={"error": {}}, checkpoint=cp)
    assert "D G1" in act
    assert src == "checkpoint_step"


def test_compute_first_act_none() -> None:
    turn = json.loads((FIXTURES / "12286-cp-turn-138.json").read_text())
    cp = parse_checkpoint_residue(turn["body"])
    act, src = compute_first_act(open_consults=[], continuity={"error": {}}, checkpoint=cp)
    assert act is None
    assert src == "none"


def test_check_packet_sections_flags_error_envelope() -> None:
    packet = assemble_packet(sections=_minimal_sections(), options={"root": "12286"})
    v = check_packet_sections(packet, allowed_kinds=ALLOWED_LIVE_ERROR_KINDS)
    assert v == []
    packet["continuity"] = {"error": {"kind": "x"}}
    assert check_packet_sections(packet, allowed_kinds=ALLOWED_LIVE_ERROR_KINDS)


def test_check_packet_sections_allows_listed_kinds() -> None:
    packet = assemble_packet(sections=_minimal_sections(), options={"root": "12286"})
    packet["meta"]["errors"] = [{"kind": "scoreboard_missing"}]
    assert check_packet_sections(packet, allowed_kinds=ALLOWED_LIVE_ERROR_KINDS) == []
    packet["meta"]["errors"] = [{"kind": "deadline_exceeded"}]
    assert len(check_packet_sections(packet, allowed_kinds=ALLOWED_LIVE_ERROR_KINDS)) == 1


def test_shrink_order() -> None:
    packet = assemble_packet(sections=_minimal_sections(), options={"root": "12286"})
    packet["lanes"] = [{"thread": str(i), "source": "bus_tail", "tail": []} for i in range(200)]
    apply_size_caps(packet)
    assert packet_nbytes(packet) <= 12288


def test_dumps_packet_ascii() -> None:
    packet = assemble_packet(sections=_minimal_sections(), options={"root": "12286"})
    raw = dumps_packet(packet)
    assert "\\u2014" in raw or "—" not in raw


def _synthetic_consult(i: int) -> dict:
    return {
        "thread": str(10_000 + (i % 500)),
        "turn": i,
        "after_turn": i,
        "subject_prefix": "HARVEST — G6",
        "subject": f"CONSULT_PENDING — G6 synthetic {i}",
        "created_at": f"2026-10-06T12:{i % 60:02d}:{i % 60:02d}Z",
        "body_fields": ["RULING:", "NEXT_ADMIT:"],
        "asks": ["ruling text " * 20],
    }


def test_shrink_protected_recipe_only() -> None:
    consult = _synthetic_consult(1)
    consult["asks"] = ["x" * 9500]
    sections = _minimal_sections(
        fetch_consults={
            "open_consults": [consult],
            "open_consults_outside_house": [],
            "truncated_meta": {},
        },
        fetch_scores={"scores": [{"slug": "s", "entry_gate": "G1", "notes": "n" * 5000}]},
    )
    packet = assemble_packet(sections=sections, options={"root": "12286"})
    assert packet_nbytes(packet) <= 12288
    assert packet["meta"]["truncated"].get("open_consults") == "recipe_only"
    assert packet["first_act"] is not None
    assert packet["reinduct"]["call"]["pipeline_id"] == "maestro-induct"
    assert "generated_at" in packet["meta"]
    item = packet["open_consults"][0]
    assert set(item) <= {"thread", "turn", "after_turn", "subject_prefix"}
    assert packet["meta"].get("oversize") is not True


def test_shrink_terminal_minimal_envelope() -> None:
    consults = [_synthetic_consult(i) for i in range(2000)]
    sections = _minimal_sections(
        fetch_consults={
            "open_consults": consults,
            "open_consults_outside_house": [],
            "truncated_meta": {},
        },
    )
    packet = assemble_packet(sections=sections, options={"root": "12286"})
    assert packet_nbytes(packet) <= 12288
    assert packet["meta"].get("oversize") is True
    assert packet["meta"]["truncated"].get("open_consults_ids", 0) > 0
    assert packet["first_act"] is not None
    assert packet["reinduct"]["trigger"]["token"] == "MAESTRO 12286"
    assert isinstance(packet["open_consults"], list)
    assert all(isinstance(x, str) and "#" in x for x in packet["open_consults"])


def test_truncation_flags() -> None:
    consults = [_synthetic_consult(i) for i in range(2000)]
    sections = _minimal_sections(
        fetch_consults={
            "open_consults": consults,
            "open_consults_outside_house": [],
            "truncated_meta": {},
        },
    )
    packet = assemble_packet(sections=sections, options={"root": "12286"})
    assert packet_nbytes(packet) <= 12288
    assert packet["meta"]["truncated"].get("open_consults_ids", 0) > 0


def test_sections_newest_first() -> None:
    journal_text = (FIXTURES / "12286-journal.md").read_text(encoding="utf-8")
    entries = parse_journal_entries(journal_text, limit=3)
    consults = [
        {
            **_synthetic_consult(900),
            "created_at": "2026-10-06T09:00:00Z",
            "thread": "12286",
            "turn": 900,
        },
        {
            **_synthetic_consult(5),
            "created_at": "2026-09-22T02:58:23Z",
            "thread": "12359",
            "turn": 5,
        },
        {
            **_synthetic_consult(12),
            "created_at": "2026-09-22T02:06:39Z",
            "thread": "12291",
            "turn": 12,
        },
    ]
    sections = _minimal_sections(
        fetch_journal={"entries": entries},
        enumerate_lanes={"lane_ids": ["12359", "12291", "12286"]},
        fetch_consults={
            "open_consults": consults,
            "open_consults_outside_house": [],
            "truncated_meta": {},
        },
        fetch_lanes={
            "lanes": [
                {"thread": "12359", "source": "bus_tail", "tail": []},
                {"thread": "12291", "source": "bus_tail", "tail": []},
            ]
        },
    )
    packet = assemble_packet(sections=sections, options={"root": "12286"})
    assert packet["journal"]["entries"] == entries
    full = parse_journal_entries(journal_text, limit=99)
    for e in packet["journal"]["entries"]:
        assert any(e["title"] == f["title"] for f in full)
    lane_threads = [int(x["thread"]) for x in packet["lanes"]]
    assert lane_threads == sorted(lane_threads, reverse=True)
    assert [int(x) for x in packet["meta"]["lane_ids"]] == sorted(
        [int(x) for x in packet["meta"]["lane_ids"]], reverse=True
    )
    assert [c["thread"] for c in packet["open_consults"]] == ["12286", "12359", "12291"]
    assert packet["open_consults"][0]["created_at"] >= packet["open_consults"][-1]["created_at"]
