"""In-flight tracker item listing and meta lifecycle."""

from __future__ import annotations

import time

from src.core.gateway.in_flight_requests import InFlightRequestTracker
from src.core.gateway.request_inflight_meta import bind_request_inflight_meta


def test_track_with_explicit_meta_and_list_ordering() -> None:
    tracker = InFlightRequestTracker()
    tracker.track_request(
        "gw",
        "req-old",
        "model/a",
        meta={"kind": "chat", "thread": "21597"},
    )
    time.sleep(0.01)
    tracker.track_request("gw", "req-new", "model/b", meta={"kind": "cloud"})
    items = tracker.list_in_flight(limit=32)
    assert len(items) == 2
    assert items[0]["request_id"] == "req-old"
    assert items[1]["request_id"] == "req-new"
    assert items[0]["kind"] == "chat"
    assert items[0]["thread"] == "21597"
    assert "started_at" in items[0]
    assert items[0]["age_s"] >= 0


def test_contextvar_meta_when_explicit_none() -> None:
    tracker = InFlightRequestTracker()
    bind_request_inflight_meta(
        {"kind": "pipeline_sync", "execution_id": "exec-1", "thread": "t1"}
    )
    tracker.track_request("gw", "req-ctx", "model/x", meta=None)
    items = tracker.list_in_flight()
    assert items[0]["kind"] == "pipeline_sync"
    assert items[0]["execution_id"] == "exec-1"
    bind_request_inflight_meta(None)


def test_complete_and_clear_gateway_remove_meta() -> None:
    tracker = InFlightRequestTracker()
    tracker.try_reserve_slot(
        "gw",
        "generation",
        "gpu",
        "req-slot",
        "model/y",
        meta={"kind": "generate_stream"},
    )
    assert tracker.list_in_flight()
    tracker.complete_request("gw", "req-slot")
    assert tracker.list_in_flight() == []

    tracker.track_request("gw", "req-a", "model/z", meta={"kind": "chat"})
    tracker.track_request("gw", "req-b", "model/z")
    cleared = tracker.clear_gateway("gw")
    assert cleared == {"req-a", "req-b"}
    assert tracker.list_in_flight() == []


def test_list_in_flight_caps_at_32() -> None:
    tracker = InFlightRequestTracker()
    for i in range(40):
        tracker.track_request("gw", f"req-{i:02d}", "model/c")
    assert len(tracker.list_in_flight(limit=32)) == 32


def test_missing_contextvar_yields_unknown_kind() -> None:
    bind_request_inflight_meta(None)
    tracker = InFlightRequestTracker()
    tracker.track_request("gw", "req-unknown", "model/d")
    assert tracker.list_in_flight()[0]["kind"] == "unknown"
