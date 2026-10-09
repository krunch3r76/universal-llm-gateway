"""Hermetic relay tests for team_dispatch claim, claim_release, and occupancy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from systems.frontier_consult.cursor_sdk_claim_dispatch import relay_house_claim
from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
)
from systems.frontier_consult.route import (
    TeamDispatchClaimBody,
    TeamDispatchClaimReleaseBody,
    TeamDispatchOccupancyBody,
    claim_id_from_generation_options,
    team_dispatch,
)


class _Resp:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def _client(status: int, body: dict, captured: dict) -> type:
    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, path: str, *, json: dict) -> _Resp:
            captured["path"] = path
            captured["json"] = json
            captured["method"] = "POST"
            return _Resp(status, body)

        async def get(self, path: str, *, params: dict) -> _Resp:
            captured["path"] = path
            captured["params"] = params
            captured["method"] = "GET"
            return _Resp(status, body)

    return _Client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("op", "status"),
    [
        ("claim", 200),
        ("claim", 409),
        ("claim", 422),
        ("claim", 503),
        ("claim_release", 200),
        ("claim_release", 404),
        ("claim_release", 409),
        ("claim_release", 422),
        ("claim_release", 503),
        ("occupancy", 200),
        ("occupancy", 422),
        ("occupancy", 503),
    ],
)
async def test_relay_maps_giw_status(op: str, status: int) -> None:
    captured: dict = {}
    body = {"code": "WORK_KEY_HELD" if status == 409 else "ok", "work_key": "todo:k"}
    client = _client(status, body, captured)
    with (
        patch(
            "systems.frontier_consult.cursor_sdk_claim_dispatch.make_async_client",
            return_value=client(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_claim_dispatch.worker_base_url",
            return_value="http://giw.test",
        ),
    ):
        ok, detail = await relay_house_claim(
            request_id="req-1",
            op=op,
            holder_agent="cursor-sdk",
            work_key="todo:k",
            thread_id="15945",
            claim_id="claim-abc",
            outcome="completed",
            ttl_s=120,
        )
    assert ok is (status == 200)
    assert int(detail["status_code"]) == status
    if op == "claim":
        assert captured["path"] == "/api/v1/cursor/claim"
        assert captured["json"]["holder_agent"] == "cursor-sdk"
        assert captured["json"]["thread_id"] == "15945"
    elif op == "claim_release":
        assert captured["path"] == "/api/v1/cursor/claim/claim-abc/release"
        assert captured["json"]["outcome"] == "completed"
    else:
        assert captured["method"] == "GET"
        assert captured["params"]["work_key"] == "todo:k"


@pytest.mark.asyncio
async def test_team_dispatch_claim_route_requires_caller() -> None:
    body = TeamDispatchClaimBody(op="claim", work_key="todo:k", thread="15945")
    response = await team_dispatch(body=body, response=AsyncMock())
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_team_dispatch_claim_forwards_holder() -> None:
    body = TeamDispatchClaimBody(
        op="claim",
        work_key="todo:k",
        thread="15945",
        caller_agent="cursor-sdk",
        ttl_s=90,
    )
    with patch(
        "systems.frontier_consult.cursor_sdk_claim_dispatch.relay_house_claim",
        new_callable=AsyncMock,
        return_value=(True, {"claim_id": "claim-1", "status_code": 200}),
    ) as relay:
        result = await team_dispatch(body=body, response=AsyncMock())
    assert relay.await_args.kwargs["holder_agent"] == "cursor-sdk"
    assert relay.await_args.kwargs["thread_id"] == "15945"
    assert result["claim_id"] == "claim-1"


@pytest.mark.asyncio
async def test_team_dispatch_release_and_occupancy_bodies() -> None:
    release = TeamDispatchClaimReleaseBody(
        op="claim_release",
        claim_id="claim-1",
        outcome="withdrawn",
        caller_agent="cursor-sdk",
    )
    occ = TeamDispatchOccupancyBody(
        op="occupancy", work_key="todo:k", caller_agent="cursor-sdk"
    )
    with patch(
        "systems.frontier_consult.cursor_sdk_claim_dispatch.relay_house_claim",
        new_callable=AsyncMock,
        return_value=(True, {"status_code": 200, "state": "idle"}),
    ) as relay:
        await team_dispatch(body=release, response=AsyncMock())
        await team_dispatch(body=occ, response=AsyncMock())
    assert relay.await_args_list[0].kwargs["outcome"] == "withdrawn"
    assert relay.await_args_list[1].kwargs["op"] == "occupancy"


def test_claim_id_from_generation_options() -> None:
    assert claim_id_from_generation_options({"claim_id": " claim-9 "}) == "claim-9"
    assert claim_id_from_generation_options({"hop_park_release": True}) is None
    assert claim_id_from_generation_options(None) is None


@pytest.mark.asyncio
async def test_generation_options_claim_id_reaches_giw_admit_body() -> None:
    captured: dict = {}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, path: str, *, json: dict) -> _Resp:
            captured["path"] = path
            captured["json"] = json
            return _Resp(202, {"dispatch_id": "d1"})

    with (
        patch(
            "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
            return_value=_Client(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_worker_dispatch.worker_base_url",
            return_value="http://giw.test",
        ),
    ):
        ok, _detail = await dispatch_cursor_sdk_worker(
            request_id="req",
            thread_id="15945",
            model="cursor/grok-4.7",
            execution_id="exec",
            packet_path="packet.md",
            handoff_contract="implement",
            claim_id="claim-house",
            hop_park_release=True,
        )
    assert ok
    assert captured["path"] == "/api/v1/cursor/dispatch"
    assert captured["json"]["claim_id"] == "claim-house"
    assert captured["json"]["hop_park_release"] is True
