"""Tests for OLN lane closeout journal (11693)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.events import LiaisonLaneCloseoutObserved
from bus_watch.induction import build_wake_induction
from bus_watch.lane_closeout import (
    _find_worker_closeout_turn,
    build_closeout_record,
    closeout_idempotency_key,
    format_closeout_body,
    maybe_emit_abandoned_on_grace_expiry,
    observe_terminal_lane_closeouts,
    parse_closeout_turn,
    post_lane_closeout,
    probe_live,
    query_lane_closeouts,
)
from bus_watch.producer_grace import ProducerGrace
from bus_watch.spawn_pending import attention_now_row


@pytest.fixture(autouse=True)
def _hermetic_live_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record builders must not probe the live 11667 ticker."""
    monkeypatch.setattr("bus_watch.lane_live.ticker_start_unix", lambda _root: None)
    monkeypatch.setattr("bus_watch.lane_live.commit_unix", lambda _sha: None)
    monkeypatch.setattr("bus_watch.lane_live.sha_on_head", lambda _sha: None)


def _worker_closeout(*, sha: str = "0e6653cdf") -> str:
    return (
        "TYPE: CLOSEOUT\n"
        "status: complete\n"
        "land_disposition: landed\n"
        f"commit: {sha}\n"
        "next: verify doorbell two-layer\n"
    )


def test_attention_now_row_skips_root_closeout_residue() -> None:
    """Unset NOW must not play the house's own LANE CLOSEOUT subject."""
    digest = {
        "root": {"id": "11667"},
        "attention": [
            {
                "id": "11667",
                "lane_role": "sub_mission",
                "status": "active",
                "terminal": True,
                "last_subject": "LANE CLOSEOUT agent-bus:12014 completed",
                "updated_at": "2026-09-21T07:33:31Z",
            },
            {
                "id": "11693",
                "lane_role": "sub_mission",
                "status": "active",
                "last_subject": "COMMISSION — OLN lane status/debrief harness",
                "updated_at": "2026-09-18T16:00:00Z",
            },
        ],
    }
    assert (
        attention_now_row(digest)
        == "agent-bus:11693 · «COMMISSION — OLN lane status/debrief harness»"
    )


def test_attention_now_row_skips_land_leftover() -> None:
    """Finished G1 LAND must not fill empty NOW (11667 sit mill on 11806)."""
    land = {
        "id": "11806",
        "lane_role": "sub_mission",
        "status": "active",
        "lifecycle": None,
        "terminal": False,
        "last_subject": "G1 LAND lane-11894 — consume ledger seam",
        "updated_at": "2026-09-20T21:13:31Z",
    }
    digest = {
        "root": {"id": "11667"},
        "attention": [
            land,
            {
                "id": "11693",
                "lane_role": "sub_mission",
                "status": "active",
                "last_subject": "COMMISSION — OLN lane status/debrief harness",
                "updated_at": "2026-09-18T16:00:00Z",
            },
        ],
    }
    assert (
        attention_now_row(digest)
        == "agent-bus:11693 · «COMMISSION — OLN lane status/debrief harness»"
    )
    assert attention_now_row({"root": {"id": "11667"}, "attention": [land]}) == ""
    owed = {
        **land,
        "id": "10561",
        "last_subject": "LAND OWED 10561 after AC-9",
    }
    assert attention_now_row({"attention": [owed]}).startswith("agent-bus:10561")


def test_attention_now_row_newest_non_terminal_sub_mission() -> None:
    digest = {
        "attention": [
            {
                "id": "11662",
                "lane_role": "sub_mission",
                "lifecycle": "completed",
                "last_subject": "retired leaf",
            },
            {
                "id": "11693",
                "lane_role": "sub_mission",
                "lifecycle": "admitted",
                "last_subject": "COMMISSION — OLN lane status/debrief harness",
                "updated_at": "2026-09-18T16:00:00Z",
            },
        ]
    }
    assert (
        attention_now_row(digest)
        == "agent-bus:11693 · «COMMISSION — OLN lane status/debrief harness»"
    )


def test_induction_empty_now_uses_attention_child() -> None:
    digest = {
        "ts": "2026-09-18T16:00:00Z",
        "root": {"id": "11667", "turns": 51},
        "register": "autonomous",
        "attention": [
            {
                "id": "11693",
                "lane_role": "sub_mission",
                "lifecycle": "admitted",
                "last_subject": "COMMISSION — OLN lane status/debrief harness",
                "updated_at": "2026-09-18T16:00:00Z",
            }
        ],
        "policy": {"induction_loaded": []},
        "budget": {"stop_class": None},
        "changed_since_last_tick": True,
    }
    text = build_wake_induction(digest)
    assert "11693" in text
    assert "COMMISSION — OLN lane status/debrief harness" in text
    assert "empty NOW is not a stop" not in text


def test_probe_live_unprobed_with_landed_sha_only() -> None:
    assert probe_live("11692", "0e6653cdf") == "unprobed"


def test_build_closeout_record_11692_specimen() -> None:
    lane = {
        "id": "11692",
        "slug": "doorbell-two-layer",
        "lifecycle": "completed",
        "status": "closed",
    }
    record = build_closeout_record(
        lane,
        parent_root="11667",
        worker_closeout_text=_worker_closeout(),
    )
    assert record["lane"] == "11692"
    assert record["parent_root"] == "11667"
    assert record["settled"] == "complete"
    assert record["landed"] == "0e6653cdf"
    assert record["live"] == "unprobed"
    assert record["next"] == "verify doorbell two-layer"
    assert record["terminal_status"] == "completed"


def test_build_closeout_record_from_sdk_json_envelope() -> None:
    body = (
        '{"schema_version":1,"status":"partial","work_outcome":"checks_failed",'
        '"landed":false,"commits_ahead":0,'
        '"evidence_uris":{"git_refs":["02b419c62ca06ff0646982e907e8fa0971f8a095"]}}'
    )
    record = build_closeout_record(
        {"id": "11697", "lifecycle": "completed", "status": "closed"},
        parent_root="11667",
        worker_closeout_text=body,
    )
    assert record["settled"] == "checks_failed"
    assert record["landed"] == "02b419c62ca06ff0646982e907e8fa0971f8a095"
    assert record.get("land_disposition") == "discard"
    assert record["live"] == "unprobed"


def test_build_closeout_record_lane_b_unlanded_not_discard() -> None:
    """12527: landed:false + commits_ahead — lane did not merge, cited SHA may be on master."""
    body = (
        '{"schema_version":1,"work_outcome":"shipped","landed":false,"commits_ahead":1,'
        '"evidence_uris":{"git_refs":["339fa9e0bff9f5fd8417d2da65dc46ca25977fb3"]}}'
    )
    record = build_closeout_record(
        {"id": "12527", "lifecycle": "completed", "status": "closed"},
        parent_root="12286",
        worker_closeout_text=body,
    )
    assert record["land_disposition"] == "unlanded"
    assert record["commits_ahead"] == 1
    assert record["landed"] == "339fa9e0bff9f5fd8417d2da65dc46ca25977fb3"


def test_find_worker_closeout_turn_max_turn_newest_first() -> None:
    """API newest-first must not pick an earlier CLOSEOUT (12527)."""
    turns = [
        {
            "turn_number": 4,
            "subject": "cursor-sdk CLOSEOUT",
            "body": '{"work_outcome":"shipped","landed":true}',
        },
        {"turn_number": 3, "subject": "progress", "body": "working"},
        {
            "turn_number": 2,
            "subject": "cursor-sdk CLOSEOUT",
            "body": '{"work_outcome":"checks_failed","landed":false}',
        },
    ]
    picked = _find_worker_closeout_turn(turns)
    assert picked is not None
    assert picked["turn_number"] == 4


def test_observe_refreshes_root_row_when_later_worker_closeout() -> None:
    """12527: turn 2 checks_failed then turn 4 shipped — second projection posts."""
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 99}}
    client.post.return_value = resp
    lane = {
        "id": "12527",
        "slug": "lane-closeout-integrity",
        "lifecycle": "completed",
        "status": "closed",
    }
    state: dict = {}
    turns_after_first = [
        {
            "turn_number": 2,
            "subject": "cursor-sdk CLOSEOUT",
            "body": '{"work_outcome":"checks_failed","landed":false}',
        },
    ]
    turns_after_second = [
        {
            "turn_number": 4,
            "subject": "cursor-sdk CLOSEOUT",
            "body": '{"work_outcome":"shipped","landed":true}',
        },
        {
            "turn_number": 2,
            "subject": "cursor-sdk CLOSEOUT",
            "body": '{"work_outcome":"checks_failed","landed":false}',
        },
    ]
    current = {"turns": turns_after_first}

    def fetch(_tid: str) -> list[dict]:
        return current["turns"]

    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        first = observe_terminal_lane_closeouts(
            "12286", [lane], state, client, fetch_turns=fetch
        )
        assert len(first) == 1
        assert first[0]["settled"] == "checks_failed"
        current["turns"] = turns_after_second
        second = observe_terminal_lane_closeouts(
            "12286", [lane], state, client, fetch_turns=fetch
        )
    assert len(second) == 1
    assert second[0]["settled"] == "shipped"
    assert client.post.call_count == 2


def test_post_lane_closeout_emits_event() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 52}}
    client.post.return_value = resp
    record = build_closeout_record(
        {"id": "11692", "lifecycle": "completed"},
        parent_root="11667",
        worker_closeout_text=_worker_closeout(),
    )
    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed") as emit:
        result = post_lane_closeout(
            client, parent_root="11667", record=record, abandoned=False
        )
    assert result == {"ok": True, "turn_number": 52}
    emit.assert_called_once()
    payload = client.post.call_args.kwargs["json"]
    assert payload["thread"] == "11667"
    assert payload["tags"] == ["lane:closeout"]
    assert "LANE CLOSEOUT" in payload["body"]


def test_observe_terminal_lane_closeouts_idempotent() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 52}}
    client.post.return_value = resp
    lane = {
        "id": "11692",
        "slug": "doorbell-two-layer",
        "lifecycle": "completed",
        "status": "closed",
    }
    state: dict = {}
    turns = [
        {"turn_number": 1, "subject": "cursor-sdk CLOSEOUT", "body": _worker_closeout()}
    ]

    def fetch(_tid: str) -> list[dict]:
        return turns

    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        first = observe_terminal_lane_closeouts(
            "11667", [lane], state, client, fetch_turns=fetch
        )
        second = observe_terminal_lane_closeouts(
            "11667", [lane], state, client, fetch_turns=fetch
        )
    assert len(first) == 1
    assert second == []
    assert client.post.call_count == 1
    key = closeout_idempotency_key("11692", "completed")
    assert key in state["lane_closeouts_emitted"]


def test_query_lane_closeouts_from_bus_turns_only() -> None:
    record = build_closeout_record(
        {"id": "11692", "lifecycle": "completed"},
        parent_root="11667",
        worker_closeout_text=_worker_closeout(),
    )
    body = format_closeout_body(record)
    client = MagicMock()
    with patch(
        "bus_watch.lane_closeout._get",
        return_value={
            "turns": [
                {
                    "turn_number": 52,
                    "subject": "LANE CLOSEOUT agent-bus:11692 completed",
                    "body": body,
                }
            ]
        },
    ):
        rows = query_lane_closeouts(client, "11667")
    assert len(rows) == 1
    assert rows[0]["lane"] == "11692"
    assert rows[0]["landed"] == "0e6653cdf"
    assert rows[0]["live"] == "unprobed"
    assert rows[0]["settled"] == "complete"


def test_parse_closeout_turn_roundtrip() -> None:
    record = build_closeout_record(
        {"id": "11692", "lifecycle": "completed"},
        parent_root="11667",
        worker_closeout_text=_worker_closeout(),
    )
    body = format_closeout_body(record)
    parsed = parse_closeout_turn(
        {"subject": "LANE CLOSEOUT agent-bus:11692 completed", "body": body}
    )
    assert parsed is not None
    assert parsed["landed"] == "0e6653cdf"


def test_grace_expiry_posts_lane_abandoned() -> None:
    clock = {"now": 0.0}

    def now_fn() -> float:
        return clock["now"]

    grace = ProducerGrace(grace_seconds=60.0, now_fn=now_fn)
    producer = {
        "state": "in_flight",
        "terminal_status": None,
        "delivery_at": None,
        "linked_at": "2026-09-18T00:00:00Z",
    }
    assert grace.observe(producer, 5) is False
    clock["now"] = 61.0
    assert grace.observe(producer, 5) is True
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 53}}
    client.post.return_value = resp
    lane = {"id": "11694", "lifecycle": "admitted", "slug": "orphan-lane"}
    state: dict = {}
    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        result = maybe_emit_abandoned_on_grace_expiry(
            grace,
            producer,
            5,
            parent_root="11667",
            lane=lane,
            state=state,
            client=client,
        )
    assert result is not None
    assert result.get("ok") is True
    payload = client.post.call_args.kwargs["json"]
    assert payload["subject"].startswith("LANE ABANDONED")


def test_lane_closeout_event_signal() -> None:
    event = LiaisonLaneCloseoutObserved(
        root="11667",
        lane="11692",
        terminal_status="completed",
        abandoned=False,
        turn=52,
    )
    assert event.signal == "liaison.lane_closeout.emitted"


def test_query_survives_without_tick_json(tmp_path) -> None:
    """AC2 — query path uses bus turns, not tmp/watchers scratch."""
    record = build_closeout_record(
        {"id": "11692", "lifecycle": "completed"},
        parent_root="11667",
        worker_closeout_text=_worker_closeout(),
    )
    body = format_closeout_body(record)
    client = MagicMock()
    with patch(
        "bus_watch.lane_closeout._get",
        return_value={"turns": [{"subject": "LANE CLOSEOUT", "body": body}]},
    ):
        rows = query_lane_closeouts(client, "11667")
    tick = tmp_path / "liaison-11667.tick.json"
    assert not tick.exists()
    assert rows[0]["landed"] == "0e6653cdf"


def test_pinned_conductor_closeout_records_stop() -> None:
    text = (
        '{"status":"partial","degraded_reason":"conductor_row_pinned",'
        '"work_outcome":"unverified","commits_ahead":0,"landed":false}'
    )
    record = build_closeout_record(
        {"id": "12614", "lifecycle": "completed"},
        parent_root="12586",
        worker_closeout_text=text,
    )
    assert record["stop"] == "ROW_PINNED"
