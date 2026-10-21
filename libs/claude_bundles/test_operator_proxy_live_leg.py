"""Offline tests for operator-proxy live-leg fold state and paging."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import pytest

from claude_bundles import operator_proxy_live_leg as live_leg_mod
from claude_bundles.operator_proxy_live_leg import (
    GOVERNING_RULE,
    LaneFoldState,
    LiveLeg,
    OpenItem,
    _filter_live_leg_items,
    fetch_turns_to_closure,
    load_fold_state,
    load_maestro_roster_text,
    project_live_leg,
    render_live_leg,
    save_fold_state,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _hermetic_fold_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect fold-state IO off the real cortex root."""
    path = tmp_path / "notes/system/operational/live-leg-fold-state.json"
    monkeypatch.setattr(
        "claude_bundles.operator_proxy_live_leg._default_fold_state_path",
        lambda: path,
    )


_TRUNCATION_LINE = (
    "(truncated: bus paging did not reach thread start; "
    "an open item may lie beyond the fetched window.)"
)


def test_ac5_open_item_set_survives_watermark() -> None:
    key = "15851:100:todo:old"
    state = {"15851": LaneFoldState(watermark=3000, open_keys=[key])}

    def _load() -> dict[str, LaneFoldState]:
        return dict(state)

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        assert after_turn == 3000
        return [], False, None

    leg = project_live_leg(
        ["15851"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=_load,
        fold_state_save=lambda _s: None,
    )
    text = render_live_leg(leg)
    assert "todo:old" in text or "15851" in text
    assert any(i.turn == 100 or "old" in i.key for i in leg.items)


def test_ac6_sixty_page_fetch_shows_truncation_line() -> None:
    calls = {"n": 0}

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, compact, mark_read
        calls["n"] += 1
        turn = after_turn + 1
        return (
            [{"turn_number": turn, "subject": "NOTE|x"}],
            True,
            turn,
        )

    _turns, truncated = fetch_turns_to_closure("1", page_fetch=_page, max_pages=60)
    assert truncated
    leg = LiveLeg(truncated=True)
    assert _TRUNCATION_LINE in render_live_leg(leg)


def test_ac7_warm_lane_at_most_two_page_calls() -> None:
    calls: list[int] = []

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        calls.append(after_turn)
        n = after_turn + 1
        return ([{"turn_number": n, "subject": "NOTE|x"}], True, n)

    state = {"12286": LaneFoldState(watermark=50, open_keys=[])}
    project_live_leg(
        ["12286"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=lambda: dict(state),
        fold_state_save=lambda _s: None,
    )
    assert len(calls) <= 2


def test_ac8_fold_state_unreadable_cold_start() -> None:
    def _load() -> dict[str, LaneFoldState] | None:
        return None

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        assert after_turn == 0
        return [], False, None

    leg = project_live_leg(
        ["12286"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=_load,
    )
    assert isinstance(leg, LiveLeg)


def test_render_declared_in_flight_header() -> None:
    assert "declared_in_flight: 3" in render_live_leg(LiveLeg(), declared_in_flight=3)


def _page_returning(turns: list[dict[str, Any]]):
    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, after_turn, last, compact, mark_read
        return turns, False, None

    return _page


def _project_saved(
    state: dict[str, LaneFoldState],
    turns: list[dict[str, Any]],
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    lane_status: str | None = None,
) -> tuple[LiveLeg, dict[str, LaneFoldState]]:
    saved: dict[str, LaneFoldState] = {}

    def _save(new_state: dict[str, LaneFoldState]) -> None:
        saved.clear()
        saved.update(new_state)

    fetch = lambda _lane: []  # noqa: E731
    if lane_status is not None:
        from claude_bundles.operator_proxy_bus_fetch import fetch_turns_for_lane

        assert monkeypatch is not None
        monkeypatch.setattr(
            live_leg_mod, "fetch_lane_status", lambda _lane: lane_status
        )
        fetch = fetch_turns_for_lane
    leg = project_live_leg(
        ["15851"],
        fetch,
        page_fetch=_page_returning(turns),
        fold_state_load=lambda: dict(state),
        fold_state_save=_save,
    )
    return leg, saved


def test_ac1_done_after_watermark_retires_open_item() -> None:
    state = {"15851": LaneFoldState(watermark=3000, open_keys=["15851:100:todo:old"])}
    leg, saved = _project_saved(
        state,
        [{"turn_number": 3001, "subject": "DONE|todo:old"}],
    )
    text = render_live_leg(leg)
    assert "todo:old" not in text
    assert all("todo:old" not in key for key in saved["15851"].open_keys)
    assert saved["15851"].watermark >= 3000


def test_ac1_close_house_retires_carried_item() -> None:
    state = {"15851": LaneFoldState(watermark=3000, open_keys=["15851:100:todo:old"])}
    leg, saved = _project_saved(
        state,
        [{"turn_number": 3002, "subject": "CLOSE|house"}],
    )
    assert "todo:old" not in render_live_leg(leg)
    assert saved["15851"].open_keys == []


def test_ac1_lane_status_closed_retires_carried_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"15851": LaneFoldState(watermark=3000, open_keys=["15851:100:todo:old"])}
    leg, saved = _project_saved(
        state,
        [],
        monkeypatch=monkeypatch,
        lane_status="closed",
    )
    assert "todo:old" not in render_live_leg(leg)
    assert saved["15851"].open_keys == []


def test_ac1_carried_pickup_and_plan_keep_their_verbs() -> None:
    state = {
        "15851": LaneFoldState(
            watermark=3000,
            open_keys=[
                "15851:150:PICKUP|todo:pick",
                "15851:160:PLAN|todo:plan",
            ],
        )
    }
    leg, _saved = _project_saved(state, [])
    text = render_live_leg(leg)
    assert "PICKUP|todo:pick" in text
    assert GOVERNING_RULE["PICKUP"].split(":")[0] in text
    assert "PLAN|todo:plan" in text
    assert GOVERNING_RULE["PLAN"].split(":")[0] in text
    pickup = next(item for item in leg.items if item.key == "todo:pick")
    assert pickup.verb == "PICKUP"
    assert "R5" not in text.split("todo:pick", 1)[1].split("\n", 1)[0]


def test_ac1_no_new_pages_keeps_watermark() -> None:
    state = {"15851": LaneFoldState(watermark=3000, open_keys=["15851:100:todo:old"])}
    leg, saved = _project_saved(state, [])
    assert any(item.key == "todo:old" and item.verb == "REQUEST" for item in leg.items)
    assert saved["15851"].watermark >= 3000


def test_ac1_legacy_three_part_key_loads_as_request() -> None:
    loaded = load_fold_state(
        read_state=lambda: json.dumps(
            {
                "lanes": {
                    "15851": {"watermark": 3000, "open_keys": ["15851:100:todo:old"]}
                }
            }
        )
    )
    assert loaded is not None
    assert loaded["15851"].open_keys == ["15851:100:todo:old"]
    leg, _saved = _project_saved(
        {"15851": loaded["15851"]},
        [],
    )
    item = leg.items[0]
    assert item.verb == "REQUEST"
    assert item.key == "todo:old"


def test_ac7_bad_watermark_cold_starts() -> None:
    loaded = load_fold_state(read_state=lambda: '{"lanes":{"1":{"watermark":"x"}}}')
    assert loaded == {}
    seen: list[int] = []

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        seen.append(after_turn)
        return [], False, None

    leg = project_live_leg(
        ["1"],
        lambda _lane: [],
        page_fetch=_page,
        fold_state_load=lambda: loaded,
        fold_state_save=lambda _s: None,
    )
    assert seen == [0]
    assert isinstance(leg, LiveLeg)


def test_ac7_bad_open_key_cold_starts() -> None:
    seen: list[int] = []

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        seen.append(after_turn)
        return [], False, None

    leg = project_live_leg(
        ["1"],
        lambda _lane: [],
        page_fetch=_page,
        fold_state_load=lambda: {
            "1": LaneFoldState(watermark=50, open_keys=["1:x:todo:a"])
        },
        fold_state_save=lambda _s: None,
    )
    assert seen == [0]
    assert leg.items == []


def test_ac7_fold_state_load_runtime_error_cold_starts() -> None:
    seen: list[int] = []

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        seen.append(after_turn)
        return [], False, None

    def _boom() -> dict[str, LaneFoldState]:
        raise RuntimeError("store down")

    leg = project_live_leg(
        ["1"],
        lambda _lane: [],
        page_fetch=_page,
        fold_state_load=_boom,
    )
    assert seen == [0]
    assert isinstance(leg, LiveLeg)


def test_ac8_corrupt_json_is_replaced(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    path = tmp_path / "notes/system/operational/live-leg-fold-state.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    project_live_leg(
        ["1"],
        lambda _lane: [],
        page_fetch=_page_returning([]),
    )
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert "lanes" in reloaded
    again = load_fold_state()
    assert again is not None
    assert again["1"].watermark == 0


def test_ac8_replace_failure_keeps_bytes_and_warns(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    path = tmp_path / "notes/system/operational/live-leg-fold-state.json"
    path.parent.mkdir(parents=True)
    original = '{"lanes": {"9": {"open_keys": [], "watermark": 4}}}\n'
    path.write_text(original, encoding="utf-8")

    def _boom(src: str, dst: str) -> None:
        del src, dst
        raise OSError("replace failed")

    monkeypatch.setattr(live_leg_mod.os, "replace", _boom)
    with caplog.at_level(logging.WARNING):
        save_fold_state({"1": LaneFoldState(watermark=1, open_keys=[])})
    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob(".live-leg-fold-state.json.*.tmp")) == []
    assert "live-leg fold state write failed" in caplog.text


def test_ac8_saves_use_unique_temp_files(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    path = tmp_path / "notes/system/operational/live-leg-fold-state.json"
    path.parent.mkdir(parents=True)
    leftover = path.parent / ".live-leg-fold-state.json.leftover.tmp"
    leftover.write_text("stale", encoding="utf-8")
    seen: list[str] = []
    real_replace = os.replace

    def _spy(src: str, dst: str) -> None:
        seen.append(src)
        real_replace(src, dst)

    monkeypatch.setattr(live_leg_mod.os, "replace", _spy)
    first = {"1": LaneFoldState(watermark=1, open_keys=["1:1:REQUEST|todo:a"])}
    second = {"1": LaneFoldState(watermark=2, open_keys=["1:2:REQUEST|todo:b"])}
    save_fold_state(first)
    save_fold_state(second)
    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(str(path.parent) == str(os.path.dirname(src)) for src in seen)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["lanes"]["1"]["watermark"] in {1, 2}
    assert leftover.read_text(encoding="utf-8") == "stale"


def test_ac8_write_state_seam_still_receives_json() -> None:
    captured: list[str] = []
    save_fold_state(
        {"1": LaneFoldState(watermark=3, open_keys=[])},
        write_state=captured.append,
    )
    assert json.loads(captured[0])["lanes"]["1"]["watermark"] == 3


def test_ac9_page_cap_sets_truncated_through_project() -> None:
    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        turn = after_turn + 1
        return ([{"turn_number": turn, "subject": "NOTE|x"}], True, turn)

    leg = project_live_leg(
        ["1"],
        lambda _lane: [],
        page_fetch=_page,
        fold_state_load=lambda: {"1": LaneFoldState(watermark=10, open_keys=[])},
        fold_state_save=lambda _s: None,
    )
    assert leg.truncated is True
    assert _TRUNCATION_LINE in render_live_leg(leg)


def test_ac4_non_utf8_fold_state_cold_starts_and_rewrites() -> None:
    path = live_leg_mod._default_fold_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff")
    leg = project_live_leg(
        ["12286"],
        lambda _l: [],
        page_fetch=_page_returning([{"turn_number": 7, "subject": "NOTE|x"}]),
    )
    assert leg.errors == {}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["lanes"]["12286"]["watermark"] == 7


def test_ac15_pointers_block_removed() -> None:
    removed = "POINTERS_" + "BLOCK"
    assert not hasattr(live_leg_mod, removed)


def test_friction_39270_roster_load_absent_vs_unreadable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cortex"
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(root))
    absent = load_maestro_roster_text()
    assert absent.text == ""
    assert absent.unreadable is None

    operational = root / "notes/system/operational"
    operational.mkdir(parents=True)
    (operational / "maestro-sidekick-roster.md").write_bytes(b"\xff")
    bad = load_maestro_roster_text()
    assert bad.text == ""
    assert bad.unreadable == "UnicodeDecodeError"


def test_l23_inflight_keys_win_over_stale_open_items() -> None:
    items = [
        OpenItem("15722", 50, "REQUEST", "investigate", "decision:old", "s"),
        OpenItem("15722", 90, "PICKUP", "", "todo:inflight", "s"),
    ]
    filtered = _filter_live_leg_items(
        items,
        inflight_work_keys=frozenset({"todo:inflight"}),
        hop_boundary_by_lane={"15722": 80},
    )
    assert [i.key for i in filtered] == ["todo:inflight"]


def test_l23_empty_inflight_zero_boundary_returns_no_full_roster() -> None:
    items = [
        OpenItem("15722", 50, "REQUEST", "", "todo:stale", "s"),
        OpenItem("15722", 90, "PICKUP", "", "todo:also-stale", "s"),
    ]
    filtered = _filter_live_leg_items(
        items,
        inflight_work_keys=frozenset(),
        hop_boundary_by_lane={"15722": 0},
    )
    assert filtered == []


def test_l23_boundary_turn_drops_rows_at_or_before_hop() -> None:
    items = [
        OpenItem("15722", 80, "REQUEST", "", "todo:stale", "s"),
        OpenItem("15722", 81, "PLAN", "", "todo:fresh", "s"),
    ]
    filtered = _filter_live_leg_items(
        items,
        inflight_work_keys=frozenset(),
        hop_boundary_by_lane={"15722": 80},
        lane_watermarks={"15722": 80},
    )
    assert [i.key for i in filtered] == ["todo:fresh"]


def test_l23_peer_lane_uses_own_watermark_not_maestro_header() -> None:
    items = [
        OpenItem("15851", 100, "REQUEST", "", "todo:peer", "s"),
        OpenItem("12286", 50, "REQUEST", "", "todo:maestro-stale", "s"),
    ]
    filtered = _filter_live_leg_items(
        items,
        inflight_work_keys=frozenset(),
        hop_boundary_by_lane={"12286": 5000},
        lane_watermarks={"15851": 90},
    )
    assert [i.key for i in filtered] == ["todo:peer"]
    assert "todo:maestro-stale" not in {i.key for i in filtered}


def test_project_live_leg_saves_open_keys_before_render_filter() -> None:
    state = {
        "15851": LaneFoldState(
            watermark=3000,
            open_keys=[f"15851:{turn}:REQUEST|todo:stale{turn}" for turn in range(1, 4)],
        )
    }
    saved: dict[str, LaneFoldState] = {}

    def _save(new_state: dict[str, LaneFoldState]) -> None:
        saved.update(new_state)

    leg = project_live_leg(
        ["15851"],
        lambda _l: [],
        page_fetch=_page_returning([]),
        fold_state_load=lambda: dict(state),
        fold_state_save=_save,
        inflight_work_keys=frozenset(),
        hop_boundary_by_lane={"12286": 1},
    )
    rendered = render_live_leg(leg)
    assert not any(f"todo:stale{turn}" in rendered for turn in range(1, 4))
    assert len(saved["15851"].open_keys) == 3


_STALE_15851_OPEN_KEYS = [
    "15851:1560:REQUEST|freeform|decision:anthropic-api-credits-use-A — stargate path check",
    "15851:1577:REQUEST|freeform|decision:anthropic-api-credits-use-C1 — harness fix leg",
    "15851:1613:PLAN|friction:39550 r2 sha f94b1b05 — VERDICT PASS_WITH_NITS",
    "15851:1631:PLAN|friction:39330|cc of #1630 (lane 17905)",
    "15851:1798:PICKUP|friction:39610 #1793 items 1-2 dispatched",
    "15851:1853:PICKUP|friction:39550 house 16416 (cursor) under f94b1b05",
    "15851:1854:PICKUP|friction:39614 residue investigate routed to house 16416",
    "15851:1983:PLAN|todo:cursor-sdk-token-efficiency-lean-a-d|Shape A PLAN ready",
    "15851:1998:PLAN|a:39687 G1+G2 implement proposal 2/2",
    "15851:2037:REQUEST|a:39707 needs owner; fable-5-1 consult on floor enforcement",
    "15851:2080:REQUEST|freeform|friction:39707: relay one question to Kaywan",
    "15851:2081:PLAN|todo:conductor-admit-pipeline-options-shape-a r-next^2",
    "15851:2083:REQUEST|freeform|friction:39668: child 4 refused step1",
    "15851:2091:REQUEST|freeform|friction:39710 range forward fixes",
    "15851:2281:REQUEST|freeform|friction:30054 — co-sign or rule",
    "15851:2300:REQUEST|freeform|todo:conductor-admit-pipeline-options-shape-a — stargate sync_restart",
]


def test_l23_15851_stale_rows_suppressed_without_inflight_section() -> None:
    fold_state = {
        "15851": LaneFoldState(watermark=3000, open_keys=_STALE_15851_OPEN_KEYS),
    }

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, after_turn, last, compact, mark_read
        return [], False, None

    leg = project_live_leg(
        ["15851"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=lambda: dict(fold_state),
        fold_state_save=lambda _s: None,
        inflight_work_keys=frozenset(),
        hop_boundary_by_lane={"15851": 2300},
    )
    text = render_live_leg(leg, declared_in_flight=0)
    assert "declared_in_flight: 0" in text
    assert "anthropic-api-credits-use-A" not in text
    assert "fable-5-1 consult on floor enforcement" not in text
    assert "conductor-admit-pipeline-options-shape-a — stargate" not in text


def test_l23_inflight_key_older_than_hop_boundary_still_shown() -> None:
    items = [
        OpenItem("15851", 1560, "REQUEST", "", "todo:decision-a", "s"),
    ]
    filtered = _filter_live_leg_items(
        items,
        inflight_work_keys=frozenset({"todo:decision-a"}),
        hop_boundary_by_lane={"15851": 2300},
    )
    assert [i.key for i in filtered] == ["todo:decision-a"]


def test_render_live_leg_keeps_lane_errors_when_item_budget_truncates() -> None:
    leg = LiveLeg(
        items=[
            OpenItem("1", 1, "REQUEST", "", "todo:a", "s"),
            OpenItem("1", 2, "REQUEST", "", "todo:b", "s"),
        ],
        errors={"9": "simulated bus down"},
        lanes_queried=["1", "9"],
    )
    out = render_live_leg(leg, max_bytes=120)
    assert "live leg unavailable for 9" in out
    assert "todo:a" in out or "[hop_budget_exceeded live_leg" in out
