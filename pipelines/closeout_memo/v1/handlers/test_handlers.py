"""Admit dedupe, delivery table, and the followup request shape."""

from __future__ import annotations

import pytest
from cdp_ask.lane_admission import lane_seat_holder

from closeout_memo.models import CloseoutMemoRequest
from pipelines.closeout_memo.v1.handlers import _ledger
from pipelines.closeout_memo.v1.handlers._transport import followup_body
from pipelines.closeout_memo.v1.handlers.deliver_policy import decide

pytestmark = pytest.mark.offline


def _request(key: str = "giw:dispatch-1") -> dict:
    memo = CloseoutMemoRequest(
        memo_key=key,
        kind="sdk_closeout",
        status="completed",
        wake_lane="12286",
        worker_thread="15091",
        dispatch_thread="12286",
        dispatch_id=key.split(":", 1)[1],
        emitted_at="2026-10-05T04:10:00Z",
    )
    return memo.model_dump()


def test_admit_dedupes_on_memo_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CLOSEOUT_MEMO_LEDGER", str(tmp_path / "memo.sqlite"))
    assert _ledger.insert_admit(_request()) == "admitted"
    assert _ledger.insert_admit(_request()) == "deduped"


def test_followup_body_has_parent_thread_and_no_identity() -> None:
    body = followup_body(parent_thread="12286", prompt_text="MEMO")
    assert body["parent_thread"] == "12286"
    assert body["purpose"] == "operator-proxy"
    assert body["reattach"] is False
    assert body["min_receipt"] == "dom_committed"
    assert body["timeout_s"] == 60
    for forbidden in ("chat_url", "registration_id", "execution_id"):
        assert forbidden not in body


def test_two_row_seat_holder_picks_later_seat_bound_at() -> None:
    """Master two-row behavior. lane_current (friction 37834) stays deferred."""
    snap = {
        "observed_at": "2026-10-05T04:10:00Z",
        "source": "active-work",
        "seat_rows": [
            {
                "purpose": "operator-proxy",
                "parent_thread": "12286",
                "registration_id": "older",
                "seat_bound_at": 10,
                "chat_url": "https://example.test/older",
            },
            {
                "purpose": "operator-proxy",
                "parent_thread": "12286",
                "registration_id": "newer",
                "seat_bound_at": 20,
                "chat_url": "https://example.test/newer",
            },
        ],
    }
    holder = lane_seat_holder(snap, "12286")
    assert holder["state"] == "conflict"
    assert holder["registration_id"] == "newer"


def test_indeterminate_harvests_before_retry() -> None:
    first = decide("indeterminate", attempts=0)
    assert first.action == "harvest"
    missed = decide("indeterminate", attempts=1, harvest_present=False, last_error="")
    assert missed.action == "retry"
    assert missed.error == "harvest_miss:1"
    again = decide(
        "indeterminate",
        attempts=2,
        harvest_present=False,
        last_error="harvest_miss:1",
    )
    assert again.action == "fallback"


def test_lane_busy_retries_then_falls_back() -> None:
    seen = ""
    for _ in range(3):
        decision = decide("lane_busy", last_error=seen, attempts=0)
        assert decision.action == "retry"
        seen = decision.error
    assert decide("lane_busy", last_error=seen, attempts=0).action == "fallback"


def test_attempt_ceiling_falls_back() -> None:
    assert decide("ok", attempts=6).action == "fallback"
