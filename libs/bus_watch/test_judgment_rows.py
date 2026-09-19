"""Judgment-tier NOW row detection and harvest helpers."""

from __future__ import annotations

import pytest

from bus_watch.judgment_rows import is_judgment_turn, judgment_now_row
from bus_watch.now_row import format_now_line, resolve_now_row
from bus_watch.spawn_pending import tip_checkpoint_turn_from_turns
from bus_watch.tick_state import absorb_operator_edits, update_state

pytestmark = pytest.mark.offline


def _turn(**over):  # noqa: ANN003, ANN202
    base = {
        "turn_number": 36,
        "from": "web-anthropic",
        "subject": "cdp reply — abc123",
        "read_at": None,
        "status": "open",
        "thread": "11738",
        "created_at": "2026-09-19T21:21:26Z",
    }
    base.update(over)
    return base


def test_is_judgment_turn_accepts_cdp_reply() -> None:
    assert is_judgment_turn(_turn(), root_id="11738")


def test_is_judgment_turn_rejects_read() -> None:
    assert not is_judgment_turn(_turn(read_at="2026-09-19T21:22:00Z"), root_id="11738")


def test_is_judgment_turn_rejects_checkpoint() -> None:
    assert not is_judgment_turn(
        _turn(subject="CHECKPOINT — hop 1"), root_id="11738"
    )


def test_is_judgment_turn_rejects_generate() -> None:
    assert not is_judgment_turn(
        _turn(**{"from": "dispatch", "subject": "cursor-sdk generate — x"}),
        root_id="11738",
    )


def test_judgment_now_row_newest_unread() -> None:
    digest = {
        "root": {
            "id": "11738",
            "turns": 39,
            "unread_turns": [
                _turn(turn_number=35, subject="cdp reply — old"),
                _turn(turn_number=36),
            ],
        },
    }
    raw, source = judgment_now_row(digest) or ("", "")
    assert source == "judgment"
    assert raw.startswith("11738#36 — cdp reply")


def test_ac1_judgment_outranks_policy_bind() -> None:
    digest = {
        "root": {
            "id": "11738",
            "turns": 39,
            "unread_turns": [_turn()],
        },
        "policy": {"now_row": "a:35559 stale bind"},
        "policy_entity_cache": {},
    }
    raw, source = resolve_now_row(digest)
    assert source == "judgment"
    line = format_now_line(raw, source, digest)
    assert line.startswith("11738#36")
    assert "a:35559" not in line


def test_ac3_policy_now_row_unchanged_when_no_judgment() -> None:
    digest = {
        "root": {"id": "11738", "turns": 39, "unread_turns": []},
        "policy": {"now_row": "a:35559"},
        "frictions": [],
        "summary_row": "",
        "attention": [],
    }
    raw, source = resolve_now_row(digest)
    assert source == "policy"
    assert raw == "a:35559"


def test_harvest_residue_subject_not_checkpoint_tip() -> None:
    turns = [
        {"turn_number": 49, "subject": "CHECKPOINT — hop 2"},
        {"turn_number": 50, "subject": "HARVEST judgment — 11738"},
    ]
    assert tip_checkpoint_turn_from_turns(turns) == 49


def test_absorb_operator_edits_stamps_now_row_set_at(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "tick.json"
    update_state(
        path,
        lambda s: s.update(
            {
                "policy": {"now_row": "old bind"},
                "now_row_set_at": "2026-09-19T10:00:00Z",
            }
        ),
    )
    loop_state = {
        "policy": {"now_row": "old bind"},
        "now_row_set_at": "2026-09-19T10:00:00Z",
    }
    update_state(path, lambda s: s.update({"policy": {"now_row": "fresh bind"}}))
    absorb_operator_edits(loop_state, path)
    assert loop_state["policy"]["now_row"] == "fresh bind"
    assert loop_state["now_row_set_at"] != "2026-09-19T10:00:00Z"


def test_hand_edited_bind_outranks_stale_judgment_when_stamped() -> None:
    digest = {
        "root": {
            "id": "11738",
            "turns": 39,
            "unread_turns": [
                _turn(created_at="2026-09-19T21:00:00Z"),
            ],
        },
        "policy": {"now_row": "a:35559 fresh bind"},
        "now_row_set_at": "2026-09-19T22:00:00Z",
        "policy_entity_cache": {},
    }
    raw, source = resolve_now_row(digest)
    assert source == "policy"
    assert raw == "a:35559 fresh bind"


def test_ac4_closed_entity_policy_yields_friction() -> None:
    digest = {
        "root": {"id": "11738", "turns": 39, "unread_turns": []},
        "policy": {"now_row": "todo:maestro-deafness-now"},
        "policy_entity_cache": {"todo:maestro-deafness-now": "closed"},
        "frictions": [
            {
                "id": "a:35628",
                "category": "protocol",
                "owner": "service:agent-bus",
                "note": "maestro deafness",
                "state": "open",
                "forcing": True,
            }
        ],
        "summary_row": "",
        "attention": [],
    }
    raw, source = resolve_now_row(digest)
    assert source == "friction"
    assert "a:35628" in raw
