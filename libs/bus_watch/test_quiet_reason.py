"""Finished quiet lanes close; in-flight ones stay open."""

from bus_watch.quiet_reason import close_unharvested_quiet_lanes


def test_close_unharvested_quiet_lane_only() -> None:
    lanes = [
        {"id": "12666", "status": "active", "quiet_reason": "closeout_unharvested"},
        {"id": "12650", "status": "active", "quiet_reason": "wip_in_flight"},
        {"id": "12672", "status": "closed", "quiet_reason": "closeout_unharvested"},
    ]
    patched: list[str] = []
    closed = close_unharvested_quiet_lanes(lanes, patch=lambda tid: patched.append(tid) or True)
    assert closed == ["12666"]
    assert patched == ["12666"]
    assert lanes[0]["status"] == "closed"
    assert lanes[1]["status"] == "active"
