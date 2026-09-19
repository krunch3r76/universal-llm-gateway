"""Friction score rows: scope gate, once-latch, disposition states, night cap, NOW row."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.friction_rows import (
    build_rows,
    dispatched_tonight,
    fold_fingerprint,
    friction_dispatch_cap,
    harvest_frictions,
    latch_rows,
    mark_friction,
    now_row,
    owned_services,
    parse_mark,
    promote,
)
from bus_watch.induction import INDUCTION_CAP, build_wake_induction
from bus_watch.spawn_pending import actionable_attention, record_spawn_service

_NIGHT = "2026-09-13"


def _raw(
    aid: int, claim: str, *, owner: str = "service:agent-bus", at: str = ""
) -> dict:
    """A summary assertion row as ``cortex(tool=assertions, intent=summary)`` returns it."""
    return {
        "id": aid,
        "entity_id": owner,
        "claim": claim,
        "confidence": "hypothesized",
        "review_status": "staged",
        "observed_at": at or f"2026-09-12T{aid % 24:02d}:00:00Z",
        "superseded_by": None,
    }


# Recorded 2026-09-13 from service:agent-bus: closure rows, a feature ask and a
# plain CORRECTION note sit beside real frictions in the same window.
_RECORDED = [
    _raw(
        33361,
        "[resolved:commit:aa3179ba] Friction #33356 closed.",
        at="2026-09-13T02:06:52Z",
    ),
    _raw(
        33358,
        "CORRECTION to a:33355 plus a proven workaround.",
        at="2026-09-12T21:13:32Z",
    ),
    _raw(
        33355,
        "[tool_error] A headless cursor-sdk seat cannot author a SEALED segment CHECKPOINT.",
        at="2026-09-12T17:07:14Z",
    ),
    _raw(
        32873,
        "[schema_gap] SEALED BUT UNCELLED — the speech tape is unreadable.",
        at="2026-09-09T05:53:22Z",
    ),
    _raw(
        32682,
        "[feature] FOLLOW-UP: Hook-side debounce per root.",
        at="2026-09-08T06:36:19Z",
    ),
]


def test_owned_services_is_declared_and_canonical() -> None:
    assert owned_services({}) == []
    assert owned_services({"owned_services": "agent-bus, cortex,agent-bus"}) == [
        "service:agent-bus",
        "service:cortex",
    ]
    assert owned_services(
        {"owned_services": ["agent_skill:liaison", "service:mcp-server"]}
    ) == [
        "agent_skill:liaison",
        "service:mcp-server",
    ]


def test_dispatch_cap_defaults_and_clamps() -> None:
    assert friction_dispatch_cap({}) == 3
    assert friction_dispatch_cap({"friction_dispatch_cap": 0}) == 0
    assert friction_dispatch_cap({"friction_dispatch_cap": "x"}) == 3


def test_build_rows_keeps_only_open_frictions_newest_first() -> None:
    """Closure rows, notes and feature asks never enter; real frictions do, newest first."""
    rows = build_rows({"service:agent-bus": _RECORDED}, {})
    assert [r["id"] for r in rows] == ["a:33355", "a:32873"]
    assert rows[0]["category"] == "tool_error"
    assert rows[0]["note"].startswith("A headless cursor-sdk seat")
    assert all(r["state"] == "open" and r["forcing"] for r in rows)


def test_scope_gate_foreign_owner_rows_never_enter() -> None:
    """AC-4: a row whose entity_id is not the queried owner is dropped even if
    the read path returned it."""
    foreign = _raw(40000, "[tool_error] elsewhere", owner="service:stargate")
    rows = build_rows({"service:agent-bus": [foreign, *_RECORDED]}, {})
    assert "a:40000" not in {r["id"] for r in rows}


def test_harvest_reads_only_owned_services() -> None:
    """AC-4: nothing is read (so nothing enters) when the charter names no owner;
    an owner read failure is a summary error, not a crash."""
    with patch("bus_watch.friction_rows._cortex_rows") as fetch:
        empty = harvest_frictions({}, {}, night_id=_NIGHT)
    fetch.assert_not_called()
    assert empty == {
        "rows": [],
        "attention": [],
        "summary": {
            "owners": [],
            "open": 0,
            "forcing": 0,
            "promoted": 0,
            "dispatch_cap": 3,
            "dispatched_tonight": 0,
            "night_id": _NIGHT,
            "window_per_owner": 100,
        },
    }
    with patch("bus_watch.friction_rows._cortex_rows", side_effect=ValueError("down")):
        broken = harvest_frictions({}, {"owned_services": "agent-bus"}, night_id=_NIGHT)
    assert (
        broken["rows"] == []
        and "service:agent-bus: ValueError: down" in broken["summary"]["error"]
    )


def test_latch_enters_once_and_cap_bounds_promotion() -> None:
    """AC-1 + AC-5: one row per tick, the newest; a promoted row is latched on
    spawn and never promoted again; nothing is promoted once the cap is spent."""
    state: dict = {}
    rows = build_rows({"service:agent-bus": _RECORDED}, state)
    first = promote(rows, cap_remaining=3)
    assert [i["id"] for i in first] == ["a:33355"]
    assert first[0]["kind"] == "friction" and first[0]["owner"] == "service:agent-bus"
    latch_rows(state, first, at=f"{_NIGHT}T08:00:00+00:00")
    assert state["friction_rows_seen"] == {"a:33355": f"{_NIGHT}T08:00:00+00:00"}
    assert dispatched_tonight(state, _NIGHT) == 1
    again = build_rows({"service:agent-bus": _RECORDED}, state)
    assert again[0]["seen_at"] and again[0]["forcing"]  # still NOW until dispositioned
    assert [i["id"] for i in promote(again, cap_remaining=3)] == ["a:32873"]
    assert promote(again, cap_remaining=0) == []


def test_record_spawn_service_latches_friction_items() -> None:
    state: dict = {"pending_spawn": {"thread_id": "10601"}}
    attention = [
        {"kind": "friction", "id": "a:33355", "owner": "service:agent-bus"},
        {"id": "10586", "unread": 1, "status": "active", "lifecycle": "admitted"},
    ]
    assert actionable_attention(attention, state=state) == attention
    record_spawn_service(state, attention)
    assert set(state["friction_rows_seen"]) == {"a:33355"}
    assert state["successor_threads"] == ["10601"]


def test_disposition_states_and_repeated_failure() -> None:
    """direct-first → in flight (not forcing); a second direct-first → REPEATED_FAILURE
    (forcing again, consult); todo-minted → waits for friction_close."""
    state: dict = {}
    at = f"{_NIGHT}T09:00:00Z"
    assert mark_friction(state, "a:33355", "direct-first", at=at)["attempts"] == 1
    rows = build_rows({"service:agent-bus": _RECORDED}, state)
    by_id = {r["id"]: r for r in rows}
    assert by_id["a:33355"]["state"] == "in_flight" and not by_id["a:33355"]["forcing"]
    assert by_id["a:32873"]["forcing"]
    mark_friction(state, "a:33355", "direct-first", at=at)
    rows = build_rows({"service:agent-bus": _RECORDED}, state)
    assert rows[0]["state"] == "repeated_failure" and rows[0]["forcing"]
    assert "never a third variant" in now_row({"frictions": rows})
    mark_friction(state, "a:33355", "todo-minted", at=at)
    rows = build_rows({"service:agent-bus": _RECORDED}, state)
    assert rows[0]["state"] == "close_pending" and not rows[0]["forcing"]
    assert state["friction_dispositions"]["a:33355"]["attempts"] == 0


def test_parse_mark_vocabulary() -> None:
    assert parse_mark("a:33355:direct-first") == ("a:33355", "direct-first")
    assert parse_mark("33355:declined") == ("a:33355", "declined")
    with pytest.raises(ValueError, match="direct-first|todo-minted|declined"):
        parse_mark("a:33355:fixed")
    with pytest.raises(ValueError, match="numeric"):
        parse_mark("todo:x:declined")


def test_fold_fingerprint_moves_only_with_forcing_rows() -> None:
    rows = build_rows({"service:agent-bus": _RECORDED}, {})
    assert fold_fingerprint("abc123", []) == "abc123"
    folded = fold_fingerprint("abc123", rows)
    assert folded != "abc123" and folded == fold_fingerprint("abc123", rows)
    quiet = [{**r, "forcing": False} for r in rows]
    assert fold_fingerprint("abc123", quiet) == "abc123"


def test_build_rows_drops_non_actionable_harvest_categories() -> None:
    """AC1/G25: actionable=false drops even when category is in HARVEST_CATEGORIES."""
    raw = _raw(
        35418,
        "[tool_error] headless seat cannot author SEALED segment CHECKPOINT",
        at="2026-09-13T12:00:00Z",
    )
    raw["actionable"] = False
    raw["defer_enqueue"] = True
    rows = build_rows({"service:agent-bus": [raw, *_RECORDED[2:4]]}, {})
    assert "a:35418" not in {r["id"] for r in rows}
    assert [r["id"] for r in rows] == ["a:33355", "a:32873"]


def test_now_row_skips_latched_forcing_rows() -> None:
    """G26/AC7: latched forcing rows are not NOW; next unlatched row wins."""
    single = build_rows({"service:agent-bus": _RECORDED[2:3]}, {})
    state: dict = {}
    latch_rows(state, promote(single, cap_remaining=3), at=f"{_NIGHT}T08:00:00Z")
    latched = build_rows({"service:agent-bus": _RECORDED[2:3]}, state)
    assert now_row({"frictions": latched}) == ""
    newer = _raw(
        35420,
        "[protocol] harvest harness promotes dropped rows",
        at="2026-09-14T01:00:00Z",
    )
    mixed = build_rows({"service:agent-bus": [newer, *_RECORDED[2:3]]}, state)
    text = now_row({"frictions": mixed})
    assert text.startswith("Friction a:35420 [protocol]")


def test_induction_undispositioned_friction_is_the_now_row() -> None:
    """AC-2: one undispositioned friction, no seat bind ⇒ NOW is a dispatch row,
    the step is the dispatch step, the block fits the 700-byte cap."""
    rows = build_rows({"service:agent-bus": _RECORDED[2:3]}, {})
    digest = {
        "ts": "2026-09-13T08:10:00Z",
        "root": {"id": "10534", "turns": 300},
        "register": "autonomous",
        "attention": promote(rows, cap_remaining=3),
        "frictions": rows,
        "watchers_complete_unrelayed": [],
        "budget": {"stop_class": None},
        "checkpoint_due": False,
        "changed_since_last_tick": True,
        "summary_row": None,
        "policy": {},
    }
    text = build_wake_induction(digest)
    assert "Event: friction a:33355 [tool_error] service:agent-bus «A headless" in text
    assert (
        "NOW: Friction a:33355 [tool_error] service:agent-bus «A headless cursor-sdk "
        "seat cannot author a SEALED segmen» → disposition direct-first | "
        "todo-minted | declined (--mark-friction a:33355:<d>; close-back "
        "friction_close on the assertion)"
    ) in text
    assert "OPERATOR_GATE" in text
    assert len(text.encode("utf-8")) <= INDUCTION_CAP
    # G26: latched forcing rows are no longer NOW once seen_at is set.
    digest["attention"] = []
    latched_rows = build_rows(
        {"service:agent-bus": _RECORDED[2:3]},
        {"friction_rows_seen": {"a:33355": f"{_NIGHT}T08:00:00Z"}},
    )
    digest["frictions"] = latched_rows
    still = build_wake_induction(digest)
    assert "Event: friction" not in still
    assert "NOW: Friction a:33355" not in still
    assert still.startswith("WAKE 10534")


@patch("bus_watch.liaison_digest.collect_watchers", return_value=[])
@patch("bus_watch.liaison_digest._health", return_value="ok")
@patch("bus_watch.liaison_digest._unread_toc", return_value=[])
@patch("bus_watch.liaison_digest._child_lanes", return_value=[])
@patch("bus_watch.liaison_digest._get")
@patch("bus_watch.liaison_digest._bus")
@patch("bus_watch.liaison_digest._measure_ide_tab", return_value=None)
@patch("bus_watch.friction_rows._cortex_rows")
def test_digest_carries_friction_rows_and_attention(
    fetch: MagicMock,
    _tab: MagicMock,
    mock_bus: MagicMock,
    mock_get: MagicMock,
    _child: MagicMock,
    _toc: MagicMock,
    _health: MagicMock,
    _watchers: MagicMock,
) -> None:
    """AC-1 end to end: the digest lists the open rows, promotes them into
    attention under the cap, and a dispositioned-and-closed row leaves on the
    next harvest."""
    from bus_watch.liaison_digest import build_digest

    fetch.return_value = _RECORDED
    mock_bus.return_value.__enter__.return_value = MagicMock()
    mock_get.return_value = {"id": "10534", "turn_count": 5, "status": "active"}
    state: dict = {
        "policy": {"owned_services": "agent-bus", "friction_dispatch_cap": 1}
    }
    digest = build_digest("10534", state, register="autonomous", budget_tokens=700000)
    fetch.assert_called_once_with("service:agent-bus")
    assert [r["id"] for r in digest["frictions"]] == ["a:33355", "a:32873"]
    assert [i["id"] for i in digest["attention"] if i.get("kind") == "friction"] == [
        "a:33355"
    ]
    assert (
        digest["friction_summary"]["open"] == 2
        and digest["friction_summary"]["promoted"] == 1
    )
    assert digest["changed_since_last_tick"] is True
    assert "NOW: Friction a:33355" in digest["induction"]
    # AC-3: friction_close supersedes the assertion → it is not in the next read.
    fetch.return_value = [r for r in _RECORDED if r["id"] != 33355]
    digest = build_digest("10534", state, register="autonomous", budget_tokens=700000)
    assert [r["id"] for r in digest["frictions"]] == ["a:32873"]
