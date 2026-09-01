"""Fork A falsifier: cursor model id cannot bypass SDK substrate authorization."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from .admission import FrontierEndpointError, resolve_cursor_sdk_generate_target
from .route import TeamDispatchGenerateBody, team_dispatch


def test_cloud_role_with_cursor_model_rejects_sdk_substrate_required() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_cursor_sdk_generate_target(
            "reviewer",
            model="cursor/claude-sonnet-5",
            request_id="req-fork-a",
        )
    err = exc_info.value
    assert err.code in {"sdk_substrate_required", "seat_unknown"}
    assert err.status_code == 422


@pytest.mark.asyncio
async def test_team_dispatch_cloud_role_cursor_model_rejects_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        role="reviewer",
        model="cursor/claude-sonnet-5",
        dispatch_thread_id="todo:arc",
        contract="light-bounded",
    )
    result = await team_dispatch(body, Response())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    payload = result.body.decode()
    assert (
        "sdk_substrate_required" in payload
        or "seat_unknown" in payload
        or "substrate_model_role_conflict" in payload
    )


@pytest.mark.asyncio
async def test_cursor_sdk_role_with_cursor_model_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock(return_value={"execution_id": "exec-1", "thread_id": "t1"})
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: __import__("pathlib").Path("/tmp/packet.md"),
    )

    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        model="cursor/claude-sonnet-5",
        dispatch_thread_id="todo:arc",
        contract="light-bounded",
        lane="A",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())

    assert result == {"execution_id": "exec-1", "thread_id": "t1"}
    sdk_mock.assert_awaited_once()
