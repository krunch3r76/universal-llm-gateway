"""Hermetic tests for team_dispatch op=steer steer=cancel_discard relay."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from systems.frontier_consult.cursor_sdk_steer_dispatch import steer_cancel_discard


@pytest.mark.asyncio
async def test_steer_cancel_discard_posts_mode_discard() -> None:
    captured: dict[str, object] = {}

    class _Resp:
        status_code = 202

        @staticmethod
        def json() -> dict[str, object]:
            return {"dispatch_id": "disp-disc-1", "park_state": "park_requested"}

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, path: str, *, json: dict[str, object]) -> _Resp:
            captured["path"] = path
            captured["json"] = json
            return _Resp()

    with (
        patch(
            "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
            return_value=_Client(),
        ),
        patch(
            "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
            return_value="http://giw.test",
        ),
    ):
        ok, detail = await steer_cancel_discard(
            request_id="req-disc",
            dispatch_id="disp-disc-1",
            reason="mistaken admit",
            actor="cursor",
        )

    assert ok
    assert captured["path"] == "/api/v1/cursor/dispatch/disp-disc-1/park"
    assert captured["json"] == {
        "reason": "mistaken admit",
        "actor": "cursor",
        "mode": "discard",
    }
    assert detail["steer"] == "cancel_discard"


@pytest.mark.asyncio
async def test_team_dispatch_steer_cancel_discard_route() -> None:
    from systems.frontier_consult.route import TeamDispatchSteerBody, team_dispatch

    body = TeamDispatchSteerBody(
        op="steer",
        dispatch_id="disp-disc-2",
        steer="cancel_discard",
        reason="void",
    )
    with patch(
        "systems.frontier_consult.route.steer_cancel_discard",
        new_callable=AsyncMock,
        return_value=(True, {"dispatch_id": "disp-disc-2", "steer": "cancel_discard"}),
    ) as steer_mock:
        response = await team_dispatch(body=body, response=AsyncMock())

    steer_mock.assert_awaited_once()
    assert response["steer"] == "cancel_discard"
