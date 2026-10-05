"""Admit dedupe, delivery table, and the followup request shape."""

from __future__ import annotations

import pytest
from cdp_ask.lane_admission import lane_seat_holder
from cdp_ask.lane_current_cse import resolve_lane_current_cse
from closeout_memo.models import CloseoutMemoRequest

from . import _ledger
from ._transport import followup_body
from .deliver import apply_decision
from .deliver_policy import decide
from .fallback import _blocks

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


def test_lane_current_resolver_picks_in_flight_page() -> None:
    """AC-6 once friction 37834 is on master. Calls the resolver; does not copy it."""
    live = "https://claude.ai/cowork/cse_live"
    drain = "https://claude.ai/cowork/cse_drain"

    def pages():
        yield 9223, live, "ws://9223"
        yield 9224, drain, "ws://9224"

    def probe(port: int, _ws: str):
        if port == 9223:
            return {"streaming": True, "stop": False, "tool_pause": False}, True
        return {"streaming": False, "stop": False, "tool_pause": False}, True

    def provenance(url: str):
        if url == live:
            return {
                "parent_thread_claim": "12286",
                "registration_id": "live-reg",
                "reason": "idle_exit",
            }
        if url == drain:
            return {
                "parent_thread_claim": "12286",
                "registration_id": "drain-reg",
                "reason": "hygiene_drain",
            }
        return None

    body = resolve_lane_current_cse(
        "12286",
        snap={},
        list_pages=pages,
        probe_page=probe,
        provenance_for=provenance,
        list_active=lambda: [],
        chat_url_for_registration=lambda _rid: None,
        purpose_for_registration=lambda _rid: "operator-proxy",
        now=lambda: 1_700_000_000.0,
    )
    assert body["state"] == "current"
    assert body["basis"] == "in_flight"
    assert body["current"]["chat_url"] == live


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


def test_claim_sets_a_delivering_lease(monkeypatch, tmp_path) -> None:
    """A restart during deliver must still be visible to the retry sweep."""
    monkeypatch.setenv("CLOSEOUT_MEMO_LEDGER", str(tmp_path / "memo.sqlite"))
    assert _ledger.insert_admit(_request("giw:lease-1")) == "admitted"
    claimed = _ledger.claim_admitted("12286", limit=5)
    assert len(claimed) == 1
    stored = _ledger.due_delivering(now=10**12)
    assert len(stored) == 1
    assert stored[0]["state"] == "delivering"
    assert stored[0]["next_at"] is not None


def test_fallback_keeps_primary_and_overflow() -> None:
    body = _blocks(
        {"text": "primary block", "overflow_bus_text": "+2 more: closeout memos"}
    )
    assert "primary block" in body
    assert "+2 more" in body


@pytest.mark.asyncio
async def test_harvest_without_a_seat_falls_back_instead_of_repasting() -> None:
    async def _followup(**kwargs):
        return {"timed_out": True, "body": None}

    async def _harvest(**kwargs):
        return None

    outcome = await apply_decision(
        memo_ids=["abc"],
        wake_lane="12286",
        prompt_text="MEMO",
        followup=_followup,
        harvest=_harvest,
    )
    assert outcome["needs_fallback"] is True
    assert outcome["error"] == "harvest_no_target"
