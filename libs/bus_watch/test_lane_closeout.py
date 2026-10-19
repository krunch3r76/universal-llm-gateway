"""Tests for OLN lane closeout journal (11693)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
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
    monkeypatch.setattr(
        "bus_watch.lane_closeout.query_lane_closeouts",
        lambda *_a, **_k: [],
    )


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
    assert record["landed"] == "NOT landed@local-master"
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
    assert record["landed"] == "NOT landed@local-master"


def test_json_envelope_landed_never_lane_tip() -> None:
    from bus_watch.lane_closeout import _parse_json_closeout_envelope
    from bus_watch.lane_live import probe_live

    unlanded = (
        '{"work_outcome":"shipped","landed":false,"commits_ahead":1,'
        '"evidence_uris":{"git_refs":["339fa9e0bff9f5fd8417d2da65dc46ca25977fb3"]}}'
    )
    parsed = _parse_json_closeout_envelope(unlanded)
    assert parsed is not None
    assert parsed["landed"] == "NOT landed@local-master"
    assert probe_live("12527", parsed["landed"], parent_root="12286") == "unprobed"

    no_landed_field = (
        '{"work_outcome":"shipped","commits_ahead":1,'
        '"evidence_uris":{"git_refs":["339fa9e0bff9f5fd8417d2da65dc46ca25977fb3"]}}'
    )
    parsed2 = _parse_json_closeout_envelope(no_landed_field)
    assert parsed2 is not None
    assert parsed2["landed"] == "NOT landed@local-master"

    landed_true = (
        '{"work_outcome":"shipped","landed":true,'
        '"evidence_uris":{"git_refs":["02b419c62ca06ff0646982e907e8fa0971f8a095"]}}'
    )
    parsed3 = _parse_json_closeout_envelope(landed_true)
    assert parsed3 is not None
    assert parsed3["landed"] == "02b419c62ca06ff0646982e907e8fa0971f8a095"


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
    assert payload["to"] == "web-anthropic"
    assert payload["tags"] == ["lane:closeout"]
    assert "LANE CLOSEOUT" in payload["body"]


def test_observe_skips_when_bus_already_has_same_closeout_empty_state() -> None:
    """State reset must not replay LANE CLOSEOUT rows already on the parent root."""
    client = MagicMock()
    lane = {
        "id": "18139",
        "slug": "lane-closeout-dedupe",
        "lifecycle": "completed",
        "status": "closed",
    }
    worker = _worker_closeout()
    record = build_closeout_record(
        lane,
        parent_root="17934",
        worker_closeout_text=worker,
    )
    existing = {**record, "terminal_status": "completed"}
    state: dict = {}
    turns = [{"turn_number": 3, "subject": "cursor-sdk CLOSEOUT", "body": worker}]

    def fetch(_tid: str) -> list[dict]:
        return turns

    with patch(
        "bus_watch.lane_closeout.query_lane_closeouts", return_value=[existing]
    ):
        with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
            posted = observe_terminal_lane_closeouts(
                "17934", [lane], state, client, fetch_turns=fetch
            )
    assert posted == []
    client.post.assert_not_called()
    assert closeout_idempotency_key("18139", "completed") in state["lane_closeouts_emitted"]


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
        "bus_watch.lane_closeout_parse._get",
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
        "bus_watch.lane_closeout_parse._get",
        return_value={"turns": [{"subject": "LANE CLOSEOUT", "body": body}]},
    ):
        rows = query_lane_closeouts(client, "11667")
    tick = tmp_path / "liaison-11667.tick.json"
    assert not tick.exists()
    assert rows[0]["landed"] == "0e6653cdf"


def test_observe_terminal_lane_closeouts_batches_bulk_abandoned() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"turn": {"turn_number": 90}}
    client.post.return_value = resp
    lanes = [
        {"id": "10923", "lifecycle": "abandoned", "updated_at": "2026-10-17T00:00:00Z"},
        {"id": "10925", "lifecycle": "abandoned", "updated_at": "2026-10-17T00:00:00Z"},
        {"id": "10927", "lifecycle": "abandoned", "updated_at": "2026-10-17T00:00:00Z"},
    ]
    state: dict = {}

    def fetch(_tid: str) -> list:
        return []

    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        posted = observe_terminal_lane_closeouts(
            "11667",
            lanes,
            state,
            client,
            fetch_turns=fetch,
            parent_thread={"status": "active", "updated_at": "2026-10-12T00:00:00Z"},
        )
    assert client.post.call_count == 1
    payload = client.post.call_args.kwargs["json"]
    assert (
        payload["subject"]
        == "LANE ABANDONED agent-bus:10923..10927 (3 abandoned)"
    )
    assert payload["to"] == "web-anthropic"
    assert len(posted) == 1
    assert posted[0]["count"] == 3
    assert posted[0]["lanes"] == ["10923", "10925", "10927"]
    for lane_id in ("10923", "10925", "10927"):
        key = closeout_idempotency_key(lane_id, "abandoned")
        assert key in state["lane_closeouts_emitted"]


def test_observe_terminal_lane_closeouts_skips_closed_or_idle_parent() -> None:
    client = MagicMock()
    lanes = [
        {"id": "11001", "lifecycle": "completed", "updated_at": "2026-10-01T00:00:00Z"},
    ]
    state_closed: dict = {}
    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        observe_terminal_lane_closeouts(
            "11667",
            lanes,
            state_closed,
            client,
            fetch_turns=lambda _tid: [],
            parent_thread={"status": "closed", "updated_at": "2026-10-12T00:00:00Z"},
        )
    assert client.post.call_count == 0
    assert closeout_idempotency_key("11001", "completed") in state_closed[
        "lane_closeouts_emitted"
    ]

    client2 = MagicMock()
    state_idle: dict = {}
    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        observe_terminal_lane_closeouts(
            "11667",
            lanes,
            state_idle,
            client2,
            fetch_turns=lambda _tid: [],
            parent_thread={
                "status": "active",
                "updated_at": "2026-09-01T00:00:00Z",
            },
        )
    assert client2.post.call_count == 0
    assert closeout_idempotency_key("11001", "completed") in state_idle[
        "lane_closeouts_emitted"
    ]


def test_observe_terminal_lane_closeouts_skips_stale_child_lane() -> None:
    client = MagicMock()
    lane = {
        "id": "11002",
        "lifecycle": "completed",
        "updated_at": "2026-09-13T16:51:58Z",
    }
    state: dict = {}
    turns = [
        {"turn_number": 1, "subject": "cursor-sdk CLOSEOUT", "body": _worker_closeout()}
    ]

    def fetch(_tid: str) -> list:
        return turns

    with patch("bus_watch.lane_closeout.emit_lane_closeout_observed"):
        posted = observe_terminal_lane_closeouts(
            "11667",
            [lane],
            state,
            client,
            fetch_turns=fetch,
            parent_thread={"status": "active", "updated_at": "2026-10-12T00:00:00Z"},
        )
    assert client.post.call_count == 0
    assert posted == []
    assert closeout_idempotency_key("11002", "completed") in state[
        "lane_closeouts_emitted"
    ]


def test_post_lane_closeout_blocks_live_httpx_client_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_lane_closeout.py::test_x")
    monkeypatch.delenv("BUS_WATCH_ALLOW_LIVE_BUS_IN_TESTS", raising=False)
    client = httpx.Client(base_url="http://localhost")
    try:
        record = build_closeout_record(
            {"id": "11692", "lifecycle": "completed"},
            parent_root="11667",
        )
        result = post_lane_closeout(
            client, parent_root="11667", record=record, abandoned=False
        )
    finally:
        client.close()
    assert result == {"ok": False, "error": "pytest_live_bus_blocked"}


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
