"""Sweep re-delivery uses the same address-retry injection as the handler."""

from __future__ import annotations

from typing import Any

import pytest
from closeout_memo.models import CloseoutMemoRequest

from . import _ledger, _transport
from .sweep import sweep_once

pytestmark = pytest.mark.offline

_STORED = "https://claude.ai/cowork/cse_stored"


def _lane_cse_none() -> dict[str, Any]:
    return {
        "timed_out": False,
        "status_code": 200,
        "body": {"ok": False, "error": "lane_cse_none", "detail": "parked"},
    }


@pytest.mark.asyncio
async def test_sweep_once_address_retries_on_due_scheduled_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CLOSEOUT_MEMO_LEDGER", str(tmp_path / "memo.sqlite"))
    memo = CloseoutMemoRequest(
        memo_key="giw:sweep-retry-1",
        kind="sdk_closeout",
        status="completed",
        wake_lane="12286",
        worker_thread="15091",
        dispatch_thread="12286",
        dispatch_id="sweep-retry-1",
        emitted_at="2026-10-05T04:10:00Z",
    )
    payload = memo.model_dump()
    memo_id = payload["memo_id"]
    assert _ledger.insert_admit(payload) == "admitted"
    _ledger.claim_admitted("12286", limit=5)
    _ledger.store_render([memo_id], text="MEMO", sha256="abc123")
    _ledger.schedule_retry([memo_id], delay_s=0, error="lane_busy:1")

    calls: list[str] = []

    async def post_followup(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup")
        return _lane_cse_none()

    async def attended(**kwargs: Any) -> dict[str, Any]:
        calls.append("attended")
        return {
            "status_code": 404,
            "body": {
                "state": "none",
                "seat_holder": {"chat_url": _STORED, "registration_id": "r1"},
            },
        }

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        calls.append("followup_by_address")
        return {
            "timed_out": False,
            "body": {
                "ok": True,
                "receipt": "dom_committed",
                "url": _STORED,
                "reattach_used": True,
            },
        }

    async def post_bus(**kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(_transport, "post_followup", post_followup)
    monkeypatch.setattr(_transport, "get_lane_attended", attended)
    monkeypatch.setattr(_transport, "post_followup_by_address", followup_by_address)
    monkeypatch.setattr(_transport, "harvest_marker", _noop_harvest)
    monkeypatch.setattr(_transport, "post_bus_turn", post_bus)

    visited = await sweep_once()
    assert visited >= 1
    assert "attended" in calls
    assert "followup_by_address" in calls
    row = _ledger.load(memo_id)
    assert row is not None
    assert row["state"] == "delivered"


async def _noop_harvest(**kwargs: Any) -> bool | None:
    return None
