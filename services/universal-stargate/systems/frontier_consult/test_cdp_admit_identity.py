"""CDP admit identity gate (a:43747 D4/D6)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cdp_admit_identity import (
    require_cdp_caller_agent,
    require_cdp_review_work_key,
)


class _Body:
    def __init__(self, **kwargs: Any) -> None:
        for key, val in kwargs.items():
            setattr(self, key, val)


def test_missing_caller_agent_refused_before_spawn_row() -> None:
    body = _Body(caller_agent=None)
    with pytest.raises(FrontierEndpointError) as exc:
        require_cdp_caller_agent(body, request_id="req-1")
    assert exc.value.code == "cdp_caller_agent_required"
    assert exc.value.status_code == 422


@pytest.mark.parametrize("pseudo", ["dispatch", "unspecified", ""])
def test_pseudo_address_caller_refused(pseudo: str) -> None:
    body = _Body(caller_agent=pseudo)
    with pytest.raises(FrontierEndpointError) as exc:
        require_cdp_caller_agent(body, request_id="req-2")
    assert exc.value.code == "cdp_caller_agent_required"


def test_reply_addressed_to_caller_agent() -> None:
    body = _Body(caller_agent="cursor-sdk-lane")
    assert require_cdp_caller_agent(body, request_id="req-3") == "cursor-sdk-lane"


def test_review_class_without_work_key_refused() -> None:
    body = _Body(work_key=None, dispatch_thread_id="23296")
    with pytest.raises(FrontierEndpointError) as exc:
        require_cdp_review_work_key(body, "code-review", request_id="req-4")
    assert exc.value.code == "cdp_review_work_key_required"


def test_confer_without_work_key_admitted() -> None:
    body = _Body(work_key=None)
    require_cdp_review_work_key(body, "confer", request_id="req-5")


@pytest.mark.asyncio
async def test_missing_caller_agent_refused_before_spawn_row_integration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Admit refuses before record_spawn when caller_agent is absent."""
    from fastapi import Response

    from systems.frontier_consult.cdp_generate import dispatch_cdp_generate
    from systems.frontier_consult.route import TeamDispatchGenerateBody

    record_spawn = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.record_spawn", record_spawn
    )
    body = TeamDispatchGenerateBody(
        op="generate",
        job="confer",
        model="cdp/opus-5.5",
        dispatch_thread_id="15426",
        prompt="Hello confer body with enough content.\n",
        caller_agent=None,
    )
    with pytest.raises(FrontierEndpointError) as exc:
        await dispatch_cdp_generate(
            request_id="req-no-caller",
            body=body,
            response=Response(),
        )
    assert exc.value.code == "cdp_caller_agent_required"
    record_spawn.assert_not_called()
