"""Submit acknowledgement must not look like a completed handoff."""

from __future__ import annotations

from cdp_ask.models import SubmitProjectAskResponse


def test_submit_response_is_admission_not_arrival() -> None:
    resp = SubmitProjectAskResponse(execution_id="abc", status="running")
    assert resp.terminal is False
    assert resp.phase == "admitted"
    assert resp.handoff_status == "awaiting_first_reply"
    dumped = resp.model_dump()
    assert dumped["terminal"] is False
    assert dumped["phase"] == "admitted"
    assert dumped["handoff_status"] == "awaiting_first_reply"
    assert dumped["status"] == "running"
