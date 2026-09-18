"""Tests for OLN lane closeout journal (11693)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bus_watch.events import LiaisonLaneCloseoutObserved
from bus_watch.induction import build_wake_induction
from bus_watch.lane_closeout import (
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
        '"evidence_uris":{"git_refs":["02b419c62ca06ff0646982e907e8fa0971f8a095"]}}'
    )
    record = build_closeout_record(
        {"id": "11697", "lifecycle": "completed", "status": "closed"},
        parent_root="11667",
        worker_closeout_text=body,
    )
    assert record["settled"] == "checks_failed"
    assert record["landed"] == "02b419c62ca06ff0646982e907e8fa0971f8a095"
    assert record["live"] == "unprobed"


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
    turns = [{"subject": "cursor-sdk CLOSEOUT", "body": _worker_closeout()}]

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
