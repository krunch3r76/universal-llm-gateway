"""wake_lane is a numeric role:root thread or 422 wake_lane_invalid."""

from __future__ import annotations

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.wake_lane import validate_wake_lane

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_wake_lane_rejects_non_numeric() -> None:
    with pytest.raises(FrontierEndpointError) as caught:
        await validate_wake_lane("not-a-thread", request_id="req-1")
    assert caught.value.code == "wake_lane_invalid"
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_wake_lane_requires_role_root() -> None:
    async def _fetch(thread_id: str) -> dict:
        return {"tags": ["role:worker"]}

    with pytest.raises(FrontierEndpointError) as caught:
        await validate_wake_lane(
            "12286", request_id="req-2", fetch_thread=_fetch
        )
    assert caught.value.code == "wake_lane_invalid"


@pytest.mark.asyncio
async def test_wake_lane_accepts_role_root() -> None:
    async def _fetch(thread_id: str) -> dict:
        return {"tags": ["role:root"]}

    await validate_wake_lane("12286", request_id="req-3", fetch_thread=_fetch)
