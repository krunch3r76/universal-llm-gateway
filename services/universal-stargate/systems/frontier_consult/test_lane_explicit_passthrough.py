"""Explicit lane passthrough from team_dispatch generate to GIW worker POST."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from systems.frontier_consult.cursor_sdk_prepared_handle import (
    PreparedCursorSdkHandle,
    handle_from_dict,
    handle_to_dict,
)
from systems.frontier_consult.cursor_sdk_worker_dispatch import (
    dispatch_cursor_sdk_worker,
    dispatch_cursor_sdk_worker_message,
)


def _minimal_handle(*, lane: str | None = None) -> PreparedCursorSdkHandle:
    return PreparedCursorSdkHandle(
        request_id="req-lane",
        execution_id="exec-lane",
        dispatch_id="disp-lane",
        thread_id="thread-lane",
        resolved_model="composer-2.5",
        role="cursor-sdk",
        family="cursor",
        platform="sdk",
        to_agent="cursor-sdk:dispatch:exec-lane",
        handoff_contract="implement",
        packet_path="tmp/packet.md",
        message=None,
        caller_agent="dispatch",
        read_only=False,
        aligned_knobs=None,
        prompt_preamble=None,
        thread_subject="subject",
        pointer_body="body",
        effective_bus_lifecycle="ephemeral",
        parent_dispatch_thread_id=None,
        dispatch_thread_id="5777",
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
        auto_review_defaulted=False,
        claimed_via_atomic=False,
        admitted=True,
        alignment_warnings=(),
        knob_resolution=(),
        nest_under=None,
        lane=lane,
        refuse_if_lease_held=False,
    )


def test_team_dispatch_generate_body_accepts_lane_b() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    body = TeamDispatchGenerateBody(
        op="generate",
        contract="implement",
        dispatch_thread_id="5777",
        seat="cursor-sdk",
        lane="B",
    )
    assert body.lane == "B"


def test_team_dispatch_generate_body_rejects_invalid_lane() -> None:
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    with pytest.raises(ValidationError):
        TeamDispatchGenerateBody(
            op="generate",
            contract="implement",
            dispatch_thread_id="5777",
            seat="cursor-sdk",
            lane="C",
        )


def test_prepared_handle_lane_roundtrip() -> None:
    handle = _minimal_handle(lane="B")
    restored = handle_from_dict(handle_to_dict(handle))
    assert restored.lane == "B"


@pytest.mark.asyncio
async def test_worker_packet_dispatch_forwards_lane(
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
        request_id="req-b",
        thread_id="thread-1",
        model="composer-2.5",
        execution_id="exec-1",
        packet_path="tmp/packet.md",
        handoff_contract="implement",
        dispatch_id="disp-b",
        lane="B",
    )
    assert ok is True
    assert captured[0]["lane"] == "B"


@pytest.mark.asyncio
async def test_worker_message_dispatch_omits_lane_when_unset(
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

    ok, _detail = await dispatch_cursor_sdk_worker_message(
        request_id="req-default",
        thread_id="thread-1",
        model="composer-2.5",
        message="hello",
        execution_id="exec-1",
        handoff_contract="light-bounded",
        dispatch_id="disp-default",
    )
    assert ok is True
    assert "lane" not in captured[0]
    assert captured[0]["handoff_contract"] == "light-bounded"
