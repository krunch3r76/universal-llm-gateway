"""Hermetic tests for top-level cursor-sdk ``lane_required``."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_lane_gate import (
    LANE_REQUIRED_CODE,
    require_cursor_sdk_checkout_lane,
)
from systems.frontier_consult.generate_wrap import GenerateWrapResult
from systems.frontier_consult.route import TeamDispatchGenerateBody, team_dispatch


def test_require_lane_raises_when_top_level_omits() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        require_cursor_sdk_checkout_lane(
            request_id="req-lane",
            lane=None,
            nest_under=None,
            contract="implement",
        )
    assert exc.value.code == LANE_REQUIRED_CODE
    assert exc.value.field == "lane"
    assert exc.value.status_code == 422


def test_require_lane_skips_nest_under_resume_of_and_wrap() -> None:
    require_cursor_sdk_checkout_lane(
        request_id="req-nest",
        lane=None,
        nest_under="parent-dispatch",
        contract="implement",
    )
    require_cursor_sdk_checkout_lane(
        request_id="req-resume",
        lane=None,
        nest_under=None,
        resume_of="parent-dispatch",
        contract="implement",
    )
    require_cursor_sdk_checkout_lane(
        request_id="req-wrap",
        lane=None,
        nest_under=None,
        contract="wrap",
    )
    require_cursor_sdk_checkout_lane(
        request_id="req-named",
        lane="A",
        nest_under=None,
        contract="light-bounded",
    )


@pytest.mark.asyncio
async def test_team_dispatch_omitted_lane_returns_422() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="5777",
        contract="implement",
        packet_path="tmp/reviews/packet.md",
    )
    result = await team_dispatch(body, Response())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert LANE_REQUIRED_CODE in result.body.decode()


@pytest.mark.asyncio
async def test_team_dispatch_nest_under_omits_lane_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock(return_value={"execution_id": "exec-nest", "thread_id": "t1"})
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: __import__("pathlib").Path("/tmp/packet.md"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        lambda **kwargs: GenerateWrapResult(packet_path=kwargs["packet_path"]),
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="5777",
        contract="implement",
        packet_path="tmp/reviews/packet.md",
        nest_under="parent-disp",
    )
    result = await team_dispatch(body, Response())
    assert isinstance(result, dict)
    assert result["execution_id"] == "exec-nest"
    assert result["thread_id"] == "t1"
    sdk_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.offline
async def test_wrap_omits_lane_still_materializes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.dispatch_cursor_sdk_generate",
        sdk_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap.prepare_implement_packet",
        lambda **kwargs: GenerateWrapResult(
            packet_path="tmp/reviews/slug-implement-packet.md",
            materialized=True,
        ),
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        contract="wrap",
        source_ref="todo:slug",
    )
    response = Response()
    result = await team_dispatch(body, response)
    assert response.status_code == 200
    assert result["contract"] == "wrap"
    sdk_mock.assert_not_awaited()
