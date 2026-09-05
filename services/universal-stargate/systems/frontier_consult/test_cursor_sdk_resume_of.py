"""``resume_of`` passthrough from team_dispatch generate to GIW worker POST."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Response
from fastapi.responses import JSONResponse

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cursor_sdk_lane_gate import (
    RESUME_OF_REQUIRES_REUSE_THREAD_CODE,
    RESUME_OF_XOR_NEST_UNDER_CODE,
    reject_resume_of_conflicts,
)
from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    _parse_worker_error,
    dispatch_cursor_sdk_worker,
)
from systems.frontier_consult.generate_wrap import GenerateWrapResult
from systems.frontier_consult.route import TeamDispatchGenerateBody, team_dispatch


def test_team_dispatch_generate_body_accepts_resume_of() -> None:
    body = TeamDispatchGenerateBody(
        op="generate",
        contract="light-bounded",
        dispatch_thread_id="9964",
        seat="cursor-sdk",
        reuse_thread="9964",
        resume_of="parent-disp-1",
        packet_path="tmp/packet.md",
    )
    assert body.resume_of == "parent-disp-1"
    assert body.reuse_thread == "9964"


def test_reject_resume_of_xor_nest_under() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        reject_resume_of_conflicts(
            request_id="req-xor",
            resume_of="parent-disp",
            nest_under="parent-disp",
            reuse_thread="9964",
        )
    assert exc.value.code == RESUME_OF_XOR_NEST_UNDER_CODE
    assert exc.value.field == "resume_of"


def test_reject_resume_of_requires_reuse_thread() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        reject_resume_of_conflicts(
            request_id="req-coreq",
            resume_of="parent-disp",
            nest_under=None,
            reuse_thread=None,
        )
    assert exc.value.code == RESUME_OF_REQUIRES_REUSE_THREAD_CODE
    assert exc.value.field == "reuse_thread"


@pytest.mark.asyncio
async def test_team_dispatch_resume_of_without_reuse_thread_returns_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.generate_wrap._resolve_packet_file",
        lambda _root, _path: __import__("pathlib").Path("/tmp/packet.md"),
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="9964",
        contract="implement",
        packet_path="tmp/reviews/packet.md",
        resume_of="parent-disp",
    )
    result = await team_dispatch(body, Response())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    payload = result.body.decode()
    assert RESUME_OF_REQUIRES_REUSE_THREAD_CODE in payload


@pytest.mark.asyncio
async def test_team_dispatch_resume_of_omits_lane_still_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_mock = AsyncMock(return_value={"execution_id": "exec-resume", "thread_id": "9964"})
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
        dispatch_thread_id="9964",
        contract="implement",
        packet_path="tmp/reviews/packet.md",
        resume_of="parent-disp",
        reuse_thread="9964",
    )
    result = await team_dispatch(body, Response())
    assert isinstance(result, dict)
    assert result["execution_id"] == "exec-resume"
    kwargs = sdk_mock.await_args.kwargs
    assert kwargs["resume_of"] == "parent-disp"
    assert kwargs["reuse_thread"] == "9964"


@pytest.mark.asyncio
async def test_worker_packet_dispatch_forwards_resume_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def _post(_url: str, *, json: dict[str, object]) -> MagicMock:
        captured.append(json)
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "{}"
        resp.json.return_value = {"admitted": True}
        return resp

    client = AsyncMock()
    client.post = _post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
        lambda *_a, **_k: client,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_worker_dispatch.worker_base_url",
        lambda: "http://worker.test",
    )

    ok, _detail = await dispatch_cursor_sdk_worker(
        request_id="req-resume",
        thread_id="9964",
        model="composer-2.5",
        execution_id="exec-resume",
        packet_path="tmp/packet.md",
        handoff_contract="light-bounded",
        dispatch_id="child-disp",
        resume_of="parent-disp",
    )
    assert ok is True
    assert captured[0]["resume_of"] == "parent-disp"
    assert captured[0]["dispatch_id"] == "child-disp"
    assert captured[0]["dispatch_id"] != captured[0]["resume_of"]


def test_parse_worker_error_surfaces_resume_reason() -> None:
    resp = MagicMock()
    resp.status_code = 422
    resp.text = '{"code":"CURSOR_RESUME_INELIGIBLE","message":"ineligible","data":{"reason":"thread_mismatch"}}'
    resp.json.return_value = {
        "code": "CURSOR_RESUME_INELIGIBLE",
        "message": "ineligible",
        "data": {"reason": "thread_mismatch"},
    }
    detail = _parse_worker_error(resp, dispatch_id="child-disp")
    assert detail["resume_reason"] == "thread_mismatch"
