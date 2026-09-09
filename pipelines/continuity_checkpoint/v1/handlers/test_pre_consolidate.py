"""Unit tests for ContinuityCheckpointPreConsolidateHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ._card_patch import validate_worker_payload

pytestmark = pytest.mark.offline


def test_validate_worker_payload_requires_mission() -> None:
    ok, reason = validate_worker_payload(
        {"card_patch": {"resume_open": "line"}, "residue": "no mission here"}
    )
    assert not ok
    assert reason == "missing_mission"


def test_validate_worker_payload_accepts_mission_in_residue() -> None:
    ok, _ = validate_worker_payload(
        {
            "card_patch": {"resume_open": "line"},
            "residue": "Mission: resume house",
        }
    )
    assert ok


@pytest.mark.asyncio
async def test_seat_residue_skips_wait() -> None:
    from .pre_consolidate import ContinuityCheckpointPreConsolidateHandler

    class _Ctx:
        execution_id = "exec-pre"
        dispatch_thread_id = "10223"
        options = {
            "thread": "10223",
            "surface": "cursor",
            "from_agent": "continuity",
            "residue": "Mission: seat residue",
        }
        outputs = {"seal": {"json": {"turn_count": 1}}, "tape": {"json": {}}}

    handler = ContinuityCheckpointPreConsolidateHandler()
    with patch(
        "handlers.pre_consolidate.stargate_post",
        new=AsyncMock(return_value=({"dispatch_id": "d1"}, 202)),
    ):
        out = await handler.execute(object(), _Ctx())
    assert out.json["residue_source"] == "seat"
