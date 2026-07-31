"""Offline tests for tick SOS CDP heal via team_dispatch purpose wire."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.model_manager.ui.controller.charter_runner.root_health import (
    FireAttemptOutcome,
)
from scripts.model_manager.ui.controller.charter_runner.tick_sos_cdp_heal import (
    build_heal_prompt,
    submit_cdp_heal,
)

pytestmark = pytest.mark.offline


def test_build_heal_prompt_includes_purpose_and_outcome() -> None:
    text = build_heal_prompt(
        "6451",
        reason="sticky_admitted",
        consecutive=3,
        detail="noop",
        fire_attempt_outcome=FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED,
        worker_thread="agent-bus:99",
    )
    assert "purpose: operator-proxy" in text
    assert "fired_bookkeeping_failed" in text
    assert "agent-bus:99" in text
    assert "Do NOT re-dispatch" in text


@pytest.mark.asyncio
async def test_submit_cdp_heal_posts_team_dispatch_with_purpose() -> None:
    captured: dict = {}

    class _Resp:
        status_code = 202
        text = ""

        @staticmethod
        def json() -> dict:
            return {"execution_id": "exec-cdp-1"}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, path: str, json: dict) -> _Resp:
            captured["path"] = path
            captured["json"] = json
            return _Resp()

    with patch(
        "scripts.model_manager.ui.controller.charter_runner.tick_sos_cdp_heal.make_async_client",
        return_value=_Client(),
    ):
        eid = await submit_cdp_heal(
            "6451",
            reason="sticky_admitted",
            consecutive=2,
            detail="x",
        )
    assert eid == "exec-cdp-1"
    assert captured["path"] == "/api/v1/team/dispatch"
    body = captured["json"]
    assert body["op"] == "generate"
    assert body["model"] == "cdp/opus-5"
    assert body["purpose"] == "operator-proxy"
    assert body["contract"] == "light-bounded"
    assert body["dispatch_thread_id"] == "6451"
    assert "purpose: operator-proxy" in body["prompt"]


@pytest.mark.asyncio
async def test_submit_cdp_heal_http_error_returns_none() -> None:
    class _Resp:
        status_code = 503
        text = "down"

        @staticmethod
        def json() -> dict:
            return {}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def post(self, *_a: object, **_k: object) -> _Resp:
            return _Resp()

    with patch(
        "scripts.model_manager.ui.controller.charter_runner.tick_sos_cdp_heal.make_async_client",
        return_value=_Client(),
    ):
        assert (
            await submit_cdp_heal("1", reason="r", consecutive=1, detail="")
        ) is None
