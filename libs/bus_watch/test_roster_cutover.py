"""P1a roster journal — per-row classify replaces scalar now_row on play path."""

from __future__ import annotations

from pathlib import Path

from bus_watch.roster import (
    classify_roster_rows,
    classify_row,
    fold_roster,
    merge_by_row_id,
    read_journal,
    roster_path,
    roster_play_rows,
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
        "turns": 1,
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


def _row(
    row_id: str,
    todo: str,
    *,
    hire: str = "auto",
    gate: str = "",
    paths: list[str] | None = None,
    last_hire_dispatch_id: str = "",
) -> dict:
    out = {
        "row_id": row_id,
        "work_key": f"todo:{todo}",
        "hire": hire,
        "gate": gate,
        "text": f"todo:{todo} row",
    }
    if paths:
        out["paths"] = paths
    if last_hire_dispatch_id:
        out["last_hire_dispatch_id"] = last_hire_dispatch_id
    return out


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
        {
            "row_id": "r1",
            "work_key": "todo:a",
            "hire": "auto",
            "gate": "",
            "text": "v1",
        },
        {
            "row_id": "r1",
            "work_key": "todo:a",
            "hire": "hold",
            "gate": "",
            "text": "v2",
        },
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
    assert verdict["hold_rows"] == [{"row_id": "row-a", "reason": "live_conductor"}]


def test_disjoint_path_siblings_both_play() -> None:
    rows = [
        _row("row-a", "a", paths=["libs/bus_watch/roster.py"]),
        _row("row-b", "b", paths=["libs/bus_watch/induction.py"]),
    ]
    digest = _digest_with_roster(rows, lanes=[])
    verdicts = classify_roster_rows(digest)
    assert verdicts[0]["decision"] == "play"
    assert verdicts[1]["decision"] == "play"
    assert len(roster_play_rows(digest)) == 2


def test_path_overlap_holds_with_other_row_id() -> None:
    rows = [
        _row("row-a", "a", paths=["libs/bus_watch/roster.py"]),
        _row("row-b", "b", paths=["libs/bus_watch/roster.py"]),
    ]
    digest = _digest_with_roster(rows, lanes=[_live_lane("a", lane_id="live-a")])
    verdict_b = classify_row(digest, digest["roster"][1])
    assert verdict_b["decision"] == "hold"
    assert verdict_b["reason"] == "path_overlap:row-a"


def test_hire_latch_releases_when_consult_continuation_owed(monkeypatch) -> None:
    """Open thread plus a quiet alarm is not a finished hire."""
    rows = [
        _row(
            "row-a",
            "a",
            paths=["libs/bus_watch/roster.py"],
            last_hire_dispatch_id="disp-once",
        ),
    ]
    digest = _digest_with_roster(
        rows,
        lanes=[
            {
                "id": "12594",
                "status": "active",
                "lifecycle": "active",
                "contract": "conductor",
                "last_from": "dispatch",
                "last_subject": "Quiet with work in flight",
            }
        ],
    )

    def _fetch(thread_id: str) -> dict:
        assert thread_id == "12594"
        return {
            "ledger_unreachable": False,
            "row": {
                "dispatch_id": "disp-once",
                "consult_pending_continue_owed": True,
                "record_json": {},
            },
        }

    monkeypatch.setattr(
        "operator_hop_harvest.ledger.fetch_latest_terminal_conductor",
        _fetch,
    )
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "play"


def test_hire_latch_releases_on_parked_transport(monkeypatch) -> None:
    """A parked lane with a different closeout id is a resume, not a finished hire."""
    rows = [
        _row(
            "row-a",
            "a",
            paths=["libs/bus_watch/roster.py"],
            last_hire_dispatch_id="disp-first",
        ),
    ]
    digest = _digest_with_roster(
        rows,
        lanes=[
            {
                "id": "12597",
                "status": "closed",
                "lifecycle": "completed",
                "contract": "conductor",
                "last_from": "cursor-sdk",
                "last_subject": "cursor-sdk CLOSEOUT 8b7998b0e864",
            }
        ],
    )

    def _fetch(thread_id: str) -> dict:
        assert thread_id == "12597"
        return {
            "ledger_unreachable": False,
            "row": {
                "dispatch_id": "disp-parked",
                "consult_pending_continue_owed": False,
                "record_json": {
                    "closeout_body": "land_disposition: unlanded\nstop: PARKED_TRANSPORT\n"
                },
            },
        }

    monkeypatch.setattr(
        "operator_hop_harvest.ledger.fetch_latest_terminal_conductor",
        _fetch,
    )
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "play"


def test_closed_park_releases_the_hire_latch_so_the_row_plays() -> None:
    """A parked conductor is a stop the next admit services, not a permanent hold."""
    rows = [
        _row(
            "row-a",
            "hop-checkpoint-latch-fixture",
            paths=["libs/bus_watch/roster.py"],
            last_hire_dispatch_id="disp-parked",
        ),
    ]
    digest = _digest_with_roster(
        rows,
        lanes=[
            {
                "id": "12680",
                "status": "closed",
                "lifecycle": "completed",
                "contract": "conductor",
                "last_subject": "Quiet with work in flight",
                "quiet_reason": "closeout_unharvested",
                "tags": ["todo:hop-checkpoint-latch-fixture", "contract:conductor"],
            }
        ],
    )
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "play"


def test_hire_latch_holds_after_dispatch_recorded() -> None:
    rows = [
        _row(
            "row-a",
            "a",
            paths=["libs/bus_watch/roster.py"],
            last_hire_dispatch_id="disp-once",
        ),
    ]
    digest = _digest_with_roster(rows, lanes=[])
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "hold"
    assert verdict["reason"] == "hire_latched"


def test_closed_park_second_sight_holds_hire_latched() -> None:
    """After the releasing lane is recorded, the same park does not play again."""
    row = _row(
        "row-a",
        "hop-checkpoint-latch-fixture",
        paths=["libs/bus_watch/roster.py"],
        last_hire_dispatch_id="disp-parked",
    )
    row["readmit_from"] = "12680"
    row["readmit_count"] = 1
    digest = _digest_with_roster(
        [row],
        lanes=[
            {
                "id": "12680",
                "status": "closed",
                "lifecycle": "completed",
                "contract": "conductor",
                "quiet_reason": "closeout_unharvested",
                "tags": ["todo:hop-checkpoint-latch-fixture"],
            }
        ],
    )
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "hold"
    assert verdict["reason"] == "hire_latched"


def test_exposed_dispatch_lane_binds_closed_park_release() -> None:
    """A lane that carries the latched dispatch id is the only closed-park release."""
    row = _row(
        "row-a",
        "alpha",
        paths=["libs/bus_watch/roster.py"],
        last_hire_dispatch_id="disp-parked",
    )
    stale = {
        "id": "9001",
        "status": "closed",
        "lifecycle": "completed",
        "contract": "conductor",
        "quiet_reason": "closeout_unharvested",
        "dispatch_id": "disp-older",
        "tags": ["todo:alpha"],
    }
    own = {
        "id": "12680",
        "status": "closed",
        "lifecycle": "completed",
        "contract": "conductor",
        "quiet_reason": "closeout_unharvested",
        "dispatch_id": "disp-parked",
        "tags": ["todo:alpha"],
    }
    held = _digest_with_roster([row], lanes=[stale])
    assert classify_row(held, held["roster"][0])["reason"] == "hire_latched"
    released = _digest_with_roster([row], lanes=[stale, own])
    verdict = classify_row(released, released["roster"][0])
    assert verdict["decision"] == "play"
    assert verdict["release_reason"] == "closed_park"
    assert verdict["release_lane_id"] == "12680"


def _quiet_tick(monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.spawn_wake.fire.page_liaison", lambda *a, **k: None)
    monkeypatch.setattr(
        "bus_watch.quiet_reason.close_unharvested_quiet_lanes_on_bus",
        lambda _lanes: [],
    )
    monkeypatch.setattr(
        "bus_watch.quiet_reason.fetch_held_execution_ids",
        lambda: None,
    )


def test_closed_lane_before_first_latch_next_tick_holds(
    tmp_path: Path, monkeypatch
) -> None:
    """A park that predates the latch does not release that latch on the next tick."""
    from bus_watch.roster import append_row, fold_roster, roster_path
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    root = "12786"
    append_row(
        roster_path(root),
        _row("row-a", "alpha", paths=["libs/bus_watch/roster.py"]),
    )
    closed = {
        "id": "9001",
        "status": "closed",
        "lifecycle": "completed",
        "turns": 4,
        "contract": "conductor",
        "quiet_reason": "closeout_unharvested",
        "tags": ["todo:alpha"],
    }
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = [closed]
    digest["policy"]["max_conductors"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    state: dict = {}
    calls: list[dict] = []

    def submit(body: dict) -> tuple[dict, int]:
        calls.append(body)
        return ({"dispatch_id": f"disp-{len(calls)}"}, 202)

    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert len(calls) == 1
    assert "prompt" not in calls[0]
    assert calls[0]["contract"] == "conductor"
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    assert digest["roster"][0]["closed_lanes_before_latch"] == ["9001"]
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert len(calls) == 1
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["reason"] == "hire_latched"
    assert "pending_spawn" not in state


def test_closed_lanes_before_latch_caps_at_32_and_keeps_the_tail(
    tmp_path: Path, monkeypatch
) -> None:
    """The exclusion list is the tail of 32. Dropped ids are not excluded."""
    from bus_watch.roster import (
        _CLOSED_LANES_BEFORE_LATCH_CAP,
        append_row,
        merge_by_row_id,
        read_journal,
        record_row_hire,
    )

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    root = "12786"
    append_row(
        roster_path(root),
        _row("row-a", "alpha", paths=["libs/bus_watch/roster.py"]),
    )
    observed = [str(i) for i in range(40)]
    record_row_hire(root, "row-a", "disp-1", closed_lane_ids=observed)
    closed = merge_by_row_id(read_journal(roster_path(root)))[0][
        "closed_lanes_before_latch"
    ]
    assert len(closed) == _CLOSED_LANES_BEFORE_LATCH_CAP
    assert closed == [str(i) for i in range(8, 40)]
    record_row_hire(root, "row-a", "disp-2", closed_lane_ids=["40"])
    closed = merge_by_row_id(read_journal(roster_path(root)))[0][
        "closed_lanes_before_latch"
    ]
    assert closed == [str(i) for i in range(9, 41)]
    assert "8" not in closed


def test_reused_lane_id_in_closed_lanes_before_latch_stays_excluded() -> None:
    """A lane id still in the list does not release, even with empty readmit_from."""
    row = _row(
        "row-a",
        "hop-checkpoint-latch-fixture",
        paths=["libs/bus_watch/roster.py"],
        last_hire_dispatch_id="disp-parked",
    )
    row["closed_lanes_before_latch"] = ["12680"]
    digest = _digest_with_roster(
        [row],
        lanes=[
            {
                "id": "12680",
                "status": "closed",
                "lifecycle": "completed",
                "contract": "conductor",
                "quiet_reason": "closeout_unharvested",
                "tags": ["todo:hop-checkpoint-latch-fixture"],
            }
        ],
    )
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "hold"
    assert verdict["reason"] == "hire_latched"


def test_two_disjoint_rows_one_tick_posts_both_then_holds(
    tmp_path: Path, monkeypatch
) -> None:
    from bus_watch.roster import append_row, fold_roster, roster_path
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    root = "12786"
    append_row(
        roster_path(root),
        _row("row-a", "a", paths=["libs/bus_watch/roster.py"]),
    )
    append_row(
        roster_path(root),
        _row("row-b", "b", paths=["libs/bus_watch/induction.py"]),
    )
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = []
    digest["policy"]["max_conductors"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    state: dict = {}
    calls: list[dict] = []

    def submit(body: dict) -> tuple[dict, int]:
        calls.append(body)
        return ({"dispatch_id": f"disp-{len(calls)}"}, 202)

    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert len(calls) == 2
    assert {body["work_key"] for body in calls} == {"todo:a", "todo:b"}
    assert all(
        body["contract"] == "conductor" and "prompt" not in body for body in calls
    )
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    ids = {row["last_hire_dispatch_id"] for row in digest["roster"]}
    assert ids == {"disp-1", "disp-2"}
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert len(calls) == 2
    assert "pending_spawn" not in state


def test_path_overlap_with_live_sibling_posts_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    from bus_watch.roster import append_row, fold_roster, roster_path
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    root = "12786"
    append_row(
        roster_path(root),
        _row("row-a", "a", paths=["libs/bus_watch/roster.py"]),
    )
    append_row(
        roster_path(root),
        _row("row-b", "b", paths=["libs/bus_watch/roster.py"]),
    )
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = [_live_lane("a", lane_id="live-a")]
    digest["policy"]["max_conductors"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    calls: list[dict] = []

    def submit(body: dict) -> tuple[dict, int]:
        calls.append(body)
        return ({"dispatch_id": "disp-should-not"}, 202)

    tick_spawn_on_wake(digest, {}, root, submit=submit)
    assert calls == []
    assert classify_row(digest, digest["roster"][1])["reason"] == "path_overlap:row-a"


def test_readmit_cap_holds_and_posts_nothing(tmp_path: Path, monkeypatch) -> None:
    from bus_watch.roster import append_row, fold_roster, roster_path
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    root = "12786"
    row = _row(
        "row-a",
        "alpha",
        paths=["libs/bus_watch/roster.py"],
        last_hire_dispatch_id="disp-parked",
    )
    row["readmit_count"] = 2
    append_row(roster_path(root), row)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = [
        {
            "id": "12680",
            "status": "closed",
            "lifecycle": "completed",
            "turns": 4,
            "contract": "conductor",
            "quiet_reason": "closeout_unharvested",
            "tags": ["todo:alpha"],
        }
    ]
    digest["policy"]["max_conductors"] = 2
    digest["policy"]["max_readmits"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    calls: list[dict] = []

    def submit(body: dict) -> tuple[dict, int]:
        calls.append(body)
        return ({"dispatch_id": "disp-again"}, 202)

    tick_spawn_on_wake(digest, {}, root, submit=submit)
    assert calls == []
    assert classify_row(digest, digest["roster"][0])["reason"] == "readmit_cap"


def _capture_hire_refuses(monkeypatch) -> list[dict]:
    refused: list[dict] = []

    def _emit(**kwargs: object) -> None:
        refused.append(dict(kwargs))

    monkeypatch.setattr("bus_watch.events.emit_roster_hire_refused", _emit)
    return refused


def _play_row_digest(tmp_path: Path, monkeypatch, root: str = "12786") -> dict:
    from bus_watch.roster import append_row, fold_roster, roster_path

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    append_row(
        roster_path(root),
        _row("row-a", "alpha", paths=["libs/bus_watch/roster.py"]),
    )
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = []
    digest["policy"]["max_conductors"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    return digest


def test_thread_id_fallback_latches_hire(tmp_path: Path, monkeypatch) -> None:
    from bus_watch.roster import fold_roster
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    root = "12786"
    digest = _play_row_digest(tmp_path, monkeypatch, root)
    calls: list[dict] = []

    def submit(body: dict) -> tuple[dict, int]:
        calls.append(body)
        return ({"thread_id": "12901"}, 202)

    tick_spawn_on_wake(digest, {}, root, submit=submit)
    assert len(calls) == 1
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    assert digest["roster"][0]["last_hire_dispatch_id"] == "12901"


def test_posted_without_id_emits_refused_and_does_not_latch(
    tmp_path: Path, monkeypatch
) -> None:
    from bus_watch.roster import fold_roster
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    root = "12786"
    digest = _play_row_digest(tmp_path, monkeypatch, root)
    refused = _capture_hire_refuses(monkeypatch)

    def submit(body: dict) -> tuple[dict, int]:
        return ({}, 202)

    tick_spawn_on_wake(digest, {}, root, submit=submit)
    assert refused == [
        {
            "row_id": "row-a",
            "work_key": "todo:alpha",
            "reason": "posted_unlatched",
        }
    ]
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    assert not digest["roster"][0].get("last_hire_dispatch_id")


def test_unparseable_work_key_is_recorded_and_not_reposted(
    tmp_path: Path, monkeypatch
) -> None:
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    root = "12786"
    digest = _play_row_digest(tmp_path, monkeypatch, root)
    refused = _capture_hire_refuses(monkeypatch)
    calls = {"n": 0}

    def submit(body: dict) -> tuple[dict, int]:
        calls["n"] += 1
        return ({"error": {"code": "work_key_unparseable", "message": "nope"}}, 422)

    state: dict = {}
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert calls["n"] == 1
    assert state["refused_work_keys"] == ["todo:alpha"]
    assert refused[-1]["reason"] == "work_key_unparseable"
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert calls["n"] == 1


def test_remint_cap_409_pages_once(tmp_path: Path, monkeypatch) -> None:
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    root = "12786"
    digest = _play_row_digest(tmp_path, monkeypatch, root)
    pages: list[tuple] = []
    monkeypatch.setattr(
        "bus_watch.spawn_wake.fire.page_liaison",
        lambda *args, **kwargs: pages.append(args),
    )
    refused = _capture_hire_refuses(monkeypatch)
    calls = {"n": 0}

    def submit(body: dict) -> tuple[dict, int]:
        calls["n"] += 1
        return (
            {
                "error": {
                    "code": "CURSOR_WORK_KEY_REMINT_CAP",
                    "message": "remint seq exceeds cap",
                }
            },
            409,
        )

    state: dict = {}
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert calls["n"] == 1
    assert len(pages) == 1
    assert "REMINT_CAP" in pages[0][1]
    assert refused[-1]["reason"] == "CURSOR_WORK_KEY_REMINT_CAP"
    assert state["remint_cap_wall"]["night_id"]
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert calls["n"] == 1
    assert len(pages) == 1


def test_dry_run_does_not_emit_classify_refuses(tmp_path: Path, monkeypatch) -> None:
    from bus_watch.roster import append_row, fold_roster, roster_path
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    root = "12786"
    row = _row(
        "row-a",
        "alpha",
        paths=["libs/bus_watch/roster.py"],
        last_hire_dispatch_id="disp-parked",
    )
    row["readmit_count"] = 2
    append_row(roster_path(root), row)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = [
        {
            "id": "12680",
            "status": "closed",
            "lifecycle": "completed",
            "turns": 4,
            "contract": "conductor",
            "quiet_reason": "closeout_unharvested",
            "tags": ["todo:alpha"],
        }
    ]
    digest["policy"]["max_conductors"] = 2
    digest["policy"]["max_readmits"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    refused = _capture_hire_refuses(monkeypatch)
    state: dict = {}
    out = tick_spawn_on_wake(
        digest, state, root, dry_run=True, submit=lambda body: ({}, 202)
    )
    assert out["action"] == "hold"
    assert refused == []
    assert "roster_classify_refused" not in state


def test_readmit_cap_refuses_once_per_row_and_reason(
    tmp_path: Path, monkeypatch
) -> None:
    from bus_watch.roster import append_row, fold_roster, roster_path
    from bus_watch.spawn_on_wake import tick_spawn_on_wake

    monkeypatch.setattr("bus_watch.roster.WATCH_DIR", tmp_path)
    _quiet_tick(monkeypatch)
    root = "12786"
    row = _row(
        "row-a",
        "alpha",
        paths=["libs/bus_watch/roster.py"],
        last_hire_dispatch_id="disp-parked",
    )
    row["readmit_count"] = 2
    append_row(roster_path(root), row)
    digest = _digest(attention=[{"id": "1", "unread": 1}])
    digest["lanes"] = [
        {
            "id": "12680",
            "status": "closed",
            "lifecycle": "completed",
            "turns": 4,
            "contract": "conductor",
            "quiet_reason": "closeout_unharvested",
            "tags": ["todo:alpha"],
        }
    ]
    digest["policy"]["max_conductors"] = 2
    digest["policy"]["max_readmits"] = 2
    digest["roster"] = fold_roster(root, digest["policy"], digest, giw_live={})
    refused = _capture_hire_refuses(monkeypatch)
    state: dict = {}

    def submit(body: dict) -> tuple[dict, int]:
        raise AssertionError("readmit_cap must not post")

    tick_spawn_on_wake(digest, state, root, submit=submit)
    tick_spawn_on_wake(digest, state, root, submit=submit)
    assert refused == [
        {"row_id": "row-a", "work_key": "todo:alpha", "reason": "readmit_cap"}
    ]
    assert "row-a:readmit_cap" in state["roster_classify_refused"]


_P3_KEY = "todo:liaison-multi-conductor-p3-multi-hire"
_P3_SCOREBOARD_SHA = "a8e02943412f898b05f8f32792ea39910306d944fae4885b7091c4a472bf05c9"
_P3_ROW_ID = "liaison-multi-conductor-p3-multi-hire"


def test_scoreboard_complete_p3_row_holds_and_does_not_post() -> None:
    """hire=auto on the p3 row holds when every G-row of sha a8e02943… is DONE."""
    import hashlib

    from implement_admission.conductor_score_locus import work_item_locus

    from bus_watch.park_harvest import load_work_item_scoreboard
    from bus_watch.spawn_wake.fire import _post_roster_hires

    tip = work_item_locus("liaison-multi-conductor-p3-multi-hire").tip_path
    assert hashlib.sha256(tip.read_bytes()).hexdigest() == _P3_SCOREBOARD_SHA
    body = load_work_item_scoreboard(_P3_KEY)
    assert body is not None
    assert "DONE" in body

    row = {
        "row_id": _P3_ROW_ID,
        "work_key": _P3_KEY,
        "hire": "auto",
        "gate": "",
        "text": (
            "hire=auto. Land 5f3c4babb is on master; do not replay that slice. "
            "G5 and G6 stay open. Next step is unwritten."
        ),
    }
    digest = _digest_with_roster([row], lanes=[])
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "hold"
    assert verdict["reason"] == "scoreboard_complete"
    assert roster_play_rows(digest) == []

    posted: list[dict] = []

    def submit(body: dict) -> tuple[dict, int]:
        posted.append(body)
        return {}, 0

    result = _post_roster_hires(digest, {}, "12586", {}, submit=submit)
    assert posted == []
    assert result["action"] == "hold"


def test_hire_hold_survives_done_scoreboard() -> None:
    row = {
        "row_id": _P3_ROW_ID,
        "work_key": _P3_KEY,
        "hire": "hold",
        "gate": "",
        "text": "house paused. hire=hold. Do not admit.",
    }
    digest = _digest_with_roster([row], lanes=[])
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "hold"
    assert verdict["reason"] == "hire_hold"


def test_open_g_row_does_not_hold_for_scoreboard(monkeypatch) -> None:
    monkeypatch.setattr(
        "bus_watch.park_harvest.load_work_item_scoreboard",
        lambda _work_key: "| G1 | x | — | OPEN | |\n",
    )
    row = _row("row-open", "scoreboard-open-fixture")
    digest = _digest_with_roster([row], lanes=[])
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["decision"] == "play"
    assert verdict["reason"] == "play_row"


def test_live_projection_keeps_holder_lost_lane_and_latch() -> None:
    from bus_watch.spawn_wake.play_classify import (
        holder_lost_finished_hire,
        live_conductor_owner,
    )

    slug = "holder-lost-projection-fixture"
    lane = {
        "id": "12951",
        "status": "active",
        "lifecycle": "admitted",
        "contract": "conductor",
        "terminal": True,
        "work_key": f"todo:{slug}",
        "last_subject": "holder_lost fc33e2ab",
    }
    projection = [{"thread_id": "12951", "op_id": "62cee299adf4-ed675f8c"}]
    row = _row("row-live", slug, last_hire_dispatch_id="disp-prior")
    digest = _digest_with_roster([row], lanes=[lane])
    digest["giw_live_projections"] = projection

    owner = live_conductor_owner(digest, slug)
    assert isinstance(owner, dict)
    assert owner.get("unsure") is False
    assert owner["lane"]["id"] == "12951"
    assert holder_lost_finished_hire(digest, slug) is False
    verdict = classify_row(digest, digest["roster"][0])
    assert verdict["reason"] == "live_conductor"
    assert "release_reason" not in verdict

    bare = _digest_with_roster([row], lanes=[lane])
    assert live_conductor_owner(bare, slug) is None
    assert holder_lost_finished_hire(bare, slug) is True
    released = classify_row(bare, bare["roster"][0])
    assert released["decision"] == "play"
    assert released["release_reason"] == "holder_lost"
