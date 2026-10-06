"""Guard: handler tests must not reach the real event-service emitter."""

from __future__ import annotations

from typing import Any

import pytest

from .deliver import apply_decision

pytestmark = pytest.mark.offline

_TEST_LANE = "999888777"


@pytest.mark.asyncio
async def test_real_event_sync_never_invoked(
    closeout_memo_event_log: list[tuple[str, dict[str, Any]]],
    real_event_sync_calls: list[int],
) -> None:
    async def followup(**kwargs: Any) -> dict[str, Any]:
        return {
            "timed_out": False,
            "body": {"ok": False, "error": "lane_cse_none"},
        }

    async def attended(**kwargs: Any) -> dict[str, Any]:
        return {"status_code": 200, "body": {"state": "current"}}

    async def followup_by_address(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("followup_by_address must not run")

    async def harvest(**kwargs: Any) -> bool | None:
        return None

    await apply_decision(
        memo_ids=["m1"],
        wake_lane=_TEST_LANE,
        prompt_text="MEMO",
        followup=followup,
        harvest=harvest,
        attended=attended,
        followup_by_address=followup_by_address,
    )
    assert real_event_sync_calls == []
    assert "address_retry" in {name for name, _ in closeout_memo_event_log}
