"""tracker_runner call-site coverage for dispatch tier threading."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from services.grokbuild_worker.models.async_dispatch import GrokbuildDispatchRequest
from services.grokbuild_worker.tracker import GrokbuildExecutionTracker, _Entry
from services.grokbuild_worker.tracker_runner import run_dispatch_task


@pytest.fixture(autouse=True)
def _no_uds(monkeypatch):
    monkeypatch.setattr(
        "services.grokbuild_worker.events._emit_uds", lambda _event: None
    )


def test_mcp_false_request_rejected_at_admission() -> None:
    with pytest.raises(ValidationError) as exc_info:
        GrokbuildDispatchRequest(
            cwd="/tmp/x",
            prompt="p",
            mode="read_only",
            mcp=False,
            tier="max",
            model=None,
        )
    assert "mcp=False" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_dispatch_task_threads_tier_to_dispatch_op() -> None:
    req = GrokbuildDispatchRequest(
        cwd="/tmp/x",
        prompt="p",
        mode="read_only",
        mcp=True,
        tier="max",
        model=None,
    )
    entry = _Entry(dispatch_id="tier-thread-1", state="pending", request=req)
    tracker = GrokbuildExecutionTracker()
    captured: dict[str, Any] = {}

    async def _dispatch(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "dispatch_id": kwargs["dispatch_id"],
            "status": "completed",
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "duration_s": 0.01,
            "sidecar_path": None,
            "metadata": {"reason_code": "", "reason": "", "mcp": True},
        }

    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=AsyncMock(side_effect=_dispatch),
    ):
        await run_dispatch_task(tracker, entry)

    assert captured["tier"] == "max"
    assert entry.state == "succeeded"
