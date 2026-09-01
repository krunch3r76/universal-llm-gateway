"""Route-level witness: CDP generate bypasses cursor-sdk pool fence."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from .route import TeamDispatchGenerateBody, team_dispatch


@pytest.mark.asyncio
async def test_cdp_generate_bypasses_cursor_sdk_pool_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cdp_mock = AsyncMock(return_value={"execution_id": "cdp-exec", "thread_id": "cdp-t"})
    monkeypatch.setattr(
        "systems.frontier_consult.route.dispatch_cdp_generate",
        cdp_mock,
    )
    fence_mock = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_pool_fence.reject_other_models_pool_generate",
        fence_mock,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        model="cdp/opus-5",
        dispatch_thread_id="todo:arc",
        contract="light-bounded",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())

    assert result == {"execution_id": "cdp-exec", "thread_id": "cdp-t"}
    cdp_mock.assert_awaited_once()
    fence_mock.assert_not_called()


@pytest.mark.asyncio
async def test_cursor_sdk_other_models_generate_rejected_at_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = AsyncMock(return_value=(True, {"dispatch_id": "d1"}))
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker",
        worker,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        worker,
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        model="cursor/claude-sonnet-5",
        dispatch_thread_id="todo:arc",
        contract="light-bounded",
        lane="B",
        prompt="prompt body",
    )
    result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    payload = result.body.decode()
    assert "other_models_pool_denied" in payload
    worker.assert_not_awaited()
