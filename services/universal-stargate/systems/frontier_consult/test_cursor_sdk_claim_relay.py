"""Hermetic relay tests for team_dispatch claim, claim_release, and occupancy."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from systems.frontier_consult.cursor_sdk_claim_dispatch import relay_house_claim
from systems.frontier_consult.cursor_sdk_generate import _finish_prepared_dispatch
from systems.frontier_consult.cursor_sdk_prepared_handle import PreparedCursorSdkHandle
from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
)
from systems.frontier_consult.route import (
    TeamDispatchClaimBody,
    TeamDispatchClaimReleaseBody,
    TeamDispatchClaimUpdateBody,
    TeamDispatchOccupancyBody,
    TeamDispatchRegistryBody,
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
        ("claim_update", 200),
        ("claim_update", 409),
        ("registry", 200),
        ("registry", 422),
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
    elif op == "claim_update":
        assert captured["path"] == "/api/v1/cursor/claim/claim-abc/update"
        assert captured["json"]["holder_agent"] == "cursor-sdk"
    elif op == "registry":
        assert captured["method"] == "GET"
        assert captured["path"] == "/api/v1/cursor/registry"
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


@pytest.mark.asyncio
async def test_team_dispatch_claim_update_and_registry_forward_fields() -> None:
    upd = TeamDispatchClaimUpdateBody(
        op="claim_update",
        claim_id="claim-1",
        work_status="abandoned",
        caller_agent="cursor-sdk",
        address_patch={"kind": "grok_bot", "agent_id": "x"},
        note="done",
    )
    reg = TeamDispatchRegistryBody(
        op="registry",
        state="live",
        window_hours=12,
        work_key_prefix="todo:",
        caller_agent="cursor-sdk",
    )
    with patch(
        "systems.frontier_consult.cursor_sdk_claim_dispatch.relay_house_claim",
        new_callable=AsyncMock,
        return_value=(True, {"status_code": 200}),
    ) as relay:
        await team_dispatch(body=upd, response=AsyncMock())
        await team_dispatch(body=reg, response=AsyncMock())
    assert relay.await_args_list[0].kwargs["work_status"] == "abandoned"
    assert relay.await_args_list[0].kwargs["address_patch"]["agent_id"] == "x"
    assert relay.await_args_list[1].kwargs["op"] == "registry"
    assert relay.await_args_list[1].kwargs["work_key_prefix"] == "todo:"


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


def _inline_message_handle(**overrides: object) -> PreparedCursorSdkHandle:
    base = {
        "request_id": "req-inline",
        "execution_id": "exec-inline",
        "dispatch_id": "disp-inline",
        "thread_id": "21752",
        "resolved_model": "composer-2.5",
        "role": "cursor-sdk",
        "family": "cursor",
        "platform": "cursor",
        "to_agent": "cursor-sdk",
        "handoff_contract": "conductor",
        "packet_path": None,
        "message": "do the thing",
        "caller_agent": "maestro",
        "read_only": False,
        "aligned_knobs": None,
        "prompt_preamble": None,
        "thread_subject": "nested",
        "pointer_body": "go",
        "effective_bus_lifecycle": "ephemeral",
        "parent_dispatch_thread_id": None,
        "dispatch_thread_id": "12286",
        "density_triage": None,
        "review_opt_out_reason_code": None,
        "auto_review_child": False,
        "auto_review_defaulted": False,
        "claimed_via_atomic": False,
        "admitted": True,
        "alignment_warnings": (),
        "knob_resolution": (),
        "hop_park_release": False,
    }
    base.update(overrides)
    return PreparedCursorSdkHandle(**base)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hop_park_release", "expect_key"),
    [(True, True), (False, False)],
)
async def test_message_dispatch_hop_park_release_in_giw_body(
    hop_park_release: bool, expect_key: bool
) -> None:
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
        ok, _detail = await dispatch_cursor_sdk_worker_message(
            request_id="req",
            thread_id="21752",
            model="composer-2.5",
            message="inline packet body",
            execution_id="exec",
            handoff_contract="implement",
            hop_park_release=hop_park_release,
        )
    assert ok
    assert captured["path"] == "/api/v1/cursor/dispatch"
    if expect_key:
        assert captured["json"]["hop_park_release"] is True
    else:
        assert "hop_park_release" not in captured["json"]


@pytest.mark.asyncio
async def test_finish_prepared_message_path_forwards_hop_park_release_kwarg() -> None:
    handle = _inline_message_handle(hop_park_release=True)
    captured: dict = {}

    async def _message(**kwargs: object) -> tuple[bool, dict]:
        captured.update(kwargs)
        return True, {"dispatch_id": handle.dispatch_id, "queued": False}

    with (
        patch(
            "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
            side_effect=_message,
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate._sdk_admit_envelope",
            return_value={"dispatch_id": handle.dispatch_id},
        ),
    ):
        await _finish_prepared_dispatch(handle)

    assert captured.get("hop_park_release") is True


@pytest.mark.asyncio
async def test_worker_refusal_posts_one_failed_bus_turn_then_raises() -> None:
    handle = _inline_message_handle(caller_agent="maestro")
    worker_detail = {
        "status_code": 409,
        "code": "CONDUCTOR_MISSION_PARKED",
        "message": "hop park not released",
        "dispatch_id": handle.dispatch_id,
    }
    bus_posts: list[dict] = []

    class _BusClient:
        async def __aenter__(self) -> _BusClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, path: str, *, json: dict, headers: dict) -> _Resp:
            bus_posts.append({"path": path, "json": json, "headers": headers})
            return _Resp(200, {"ok": True})

    from systems.frontier_consult.admission import FrontierEndpointError

    with (
        patch.dict("os.environ", {"ALLOW_UNSET_AGENT_BUS_TOKEN": "1"}, clear=False),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
            new=AsyncMock(return_value=(False, worker_detail)),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.rollback_admitted_dispatch_links",
            new=AsyncMock(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
            return_value=_BusClient(),
        ),
        pytest.raises(FrontierEndpointError) as exc_info,
    ):
        await _finish_prepared_dispatch(handle)

    assert exc_info.value.code == "CONDUCTOR_MISSION_PARKED"
    assert len(bus_posts) == 1
    turn = bus_posts[0]
    assert turn["path"] == "/turns"
    assert turn["json"]["thread"] == "21752"
    assert turn["json"]["to"] == "maestro"
    subject = turn["json"]["subject"]
    assert subject.startswith("FAILED|")
    assert "exec-inline" in subject
    assert "disp-inline" in subject
    assert "CONDUCTOR_MISSION_PARKED" in subject


@pytest.mark.asyncio
async def test_worker_refusal_bus_post_error_still_raises_worker_error() -> None:
    handle = _inline_message_handle()
    worker_detail = {
        "status_code": 409,
        "code": "CONDUCTOR_MISSION_PARKED",
        "message": "parked",
    }

    class _BusClient:
        async def __aenter__(self) -> _BusClient:
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, path: str, *, json: dict, headers: dict) -> _Resp:
            raise httpx.ConnectError("bus down")

    from systems.frontier_consult.admission import FrontierEndpointError

    with (
        patch.dict("os.environ", {"ALLOW_UNSET_AGENT_BUS_TOKEN": "1"}, clear=False),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
            new=AsyncMock(return_value=(False, worker_detail)),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.rollback_admitted_dispatch_links",
            new=AsyncMock(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_worker_dispatch.make_async_client",
            return_value=_BusClient(),
        ),
        pytest.raises(FrontierEndpointError) as exc_info,
    ):
        await _finish_prepared_dispatch(handle)

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CONDUCTOR_MISSION_PARKED"
