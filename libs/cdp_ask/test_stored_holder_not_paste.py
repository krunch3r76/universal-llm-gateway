"""AC4–AC5: stored seat holder is a distinct non-paste basis."""

from __future__ import annotations

import pytest

from cdp_ask.followup_resolve import _lane_seat_followup_gate
from cdp_ask.models import FollowupProjectAskRequest


def test_followup_refuses_stored_seat_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cdp_ask.followup_resolve.emit_followup_event", lambda _e: None)
    req = FollowupProjectAskRequest(parent_thread="12286", prompt_text="paste")
    body = {
        "state": "stored",
        "basis": "stored_seat_holder",
        "reason": "seat_holder_stored",
        "current": None,
        "seat_holder": {
            "registration_id": "holder-reg",
            "evidence_class": "stored_association",
        },
        "candidates": [],
    }
    _req, err, _via = _lane_seat_followup_gate(
        req, lane_pin={"body": body}, snap={"seat_rows": []}
    )
    assert err is not None
    assert err.error == "lane_cse_stored_holder"
    assert err.error != "lane_cse_none"
