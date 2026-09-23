"""P1a roster journal — per-row classify replaces scalar now_row on play path."""

from __future__ import annotations

from pathlib import Path

import pytest

from bus_watch.roster import (
    append_row,
    classify_row,
    fold_roster,
    merge_by_row_id,
    read_journal,
    roster_path,
    roster_play_todo,
    seed_row_from_now_row,
)
from bus_watch.spawn_wake.play_classify import (
    LEFTOVER_HOLD,
    LEFTOVER_PLAY,
    PLAY_HOLD,
    classify_leftover,
)
from bus_watch.test_spawn_on_wake import _digest


def _live_lane(todo: str, *, lane_id: str = "11800") -> dict:
    return {
        "id": lane_id,
        "status": "active",
        "lifecycle": "admitted",
        "contract": "conductor",
        "work_key": f"todo:{todo}",
    }


def _unsure_lane(todo: str) -> dict:
    return {
        "id": "12599",
        "status": "active",
        "lifecycle": "admitted",
        "contract": "",
        "work_key": f"todo:{todo}",
    }


def _row(row_id: str, todo: str, *, hire: str = "auto", gate: str = "") -> dict:
    return {
        "row_id": row_id,
        "work_key": f"todo:{todo}",
        "hire": hire,
        "gate": gate,
        "text": f"todo:{todo} row",
    }


def _digest_with_roster(
    rows: list[dict],
    *,
    lanes: list[dict] | None = None,
    policy: dict | None = None,
) -> dict:
    digest = _digest()
    digest["lanes"] = lanes if lanes is not None else []
    digest["policy"] = {**(digest.get("policy") or {}), **(policy or {})}
    digest["roster"] = [{**row, "live": _row_live(digest, row)} for row in rows]
    return digest


def _row_live(digest: dict, row: dict) -> bool | None:
    from bus_watch.roster import enrich_row_liveness

    enriched = enrich_row_liveness(row, digest, {})
    return enriched.get("live")


def test_conductor_a_live_row_b_auto_plays_b_holds_a_only() -> None:
    rows = [_row("row-a", "a"), _row("row-b", "b")]
    digest = _digest_with_roster(rows, lanes=[_live_lane("a", lane_id="11800")])
    assert classify_row(digest, digest["roster"][0])["decision"] == "hold"
    assert classify_row(digest, digest["roster"][1])["decision"] == "play"
    slug, _ = roster_play_todo(digest)
    assert slug == "b"
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_PLAY
    assert verdict["todo"] == "b"


def test_unsure_on_a_holds_a_only_b_still_plays() -> None:
    rows = [_row("row-a", "a"), _row("row-b", "b")]
    digest = _digest_with_roster(rows, lanes=[_unsure_lane("a")])
    assert classify_row(digest, digest["roster"][0])["reason"] == "unsure_live"
    assert classify_row(digest, digest["roster"][1])["decision"] == "play"


def test_third_row_holds_with_max_conductors_when_two_live() -> None:
    rows = [_row("row-a", "a"), _row("row-b", "b"), _row("row-c", "c")]
    digest = _digest_with_roster(
        rows,
        lanes=[
            _live_lane("a", lane_id="1"),
            _live_lane("b", lane_id="2"),
        ],
    )
    verdict_c = classify_row(digest, digest["roster"][2])
    assert verdict_c["decision"] == "hold"
    assert verdict_c["reason"] == "max_conductors"


def test_friction_gate_on_a_does_not_hold_b() -> None:
    rows = [
        _row("row-a", "gate-todo", gate="a:40001"),
        _row("row-b", "other-todo"),
    ]
    digest = _digest_with_roster(
        rows,
        lanes=[_live_lane("gate-todo")],
        policy={"now_row": "Friction a:40001 [bug] service:bus_watch «gate»"},
    )
    assert classify_row(digest, digest["roster"][0])["decision"] == "hold"
    assert classify_row(digest, digest["roster"][1])["decision"] == "play"


def test_empty_journal_now_row_seeds_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    root = "12589"
    path = roster_path(root)
    policy = {"now_row": "todo:seed-slug G4"}
    digest = _digest()
    digest["lanes"] = []
    first = fold_roster(root, policy, digest, giw_live={})
    assert len(first) == 1
    assert first[0]["work_key"] == "todo:seed-slug"
    assert path.is_file()
    assert len(read_journal(path)) == 1
    second = fold_roster(root, policy, digest, giw_live={})
    assert len(second) == 1
    assert len(read_journal(path)) == 1


def test_merge_by_row_id_last_line_wins() -> None:
    rows = [
        {"row_id": "r1", "work_key": "todo:a", "hire": "auto", "gate": "", "text": "v1"},
        {"row_id": "r1", "work_key": "todo:a", "hire": "hold", "gate": "", "text": "v2"},
    ]
    merged = merge_by_row_id(rows)
    assert len(merged) == 1
    assert merged[0]["hire"] == "hold"


def test_seed_row_from_now_row_extracts_friction_gate() -> None:
    row = seed_row_from_now_row("Friction a:99 [bug] todo:alpha")
    assert row["gate"] == "a:99"
    assert row["work_key"] == "todo:alpha"


def test_all_roster_rows_hold_yields_play_hold_leftover() -> None:
    rows = [_row("row-a", "a")]
    digest = _digest_with_roster(rows, lanes=[_live_lane("a")])
    verdict = classify_leftover(digest, {})
    assert verdict["leftover"] == LEFTOVER_HOLD
    assert verdict["reason"] == PLAY_HOLD
