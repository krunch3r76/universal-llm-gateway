"""Paste authorization, identity refusal, and idempotent replay tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cdp_ask.cse_session_models import PasteRequest
from cdp_ask.cse_session_paste import _IDEMPOTENCY, execute_paste
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.models import FollowupProjectAskResponse


@pytest.fixture(autouse=True)
def _clear_idempotency() -> None:
    _IDEMPOTENCY.clear()
    yield
    _IDEMPOTENCY.clear()


@pytest.mark.asyncio
async def test_paste_identity_omission_refused() -> None:
    result = await execute_paste(PasteRequest(prompt_text="hi"), ExecutionStore())
    assert result.code == "identity_required"


@pytest.mark.asyncio
async def test_unauthorized_cross_lane_403_shape() -> None:
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={
                "state": "current",
                "registration_id": "target-reg",
                "parent_thread_claim": "lane-1",
            },
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(registration_id="target-reg", prompt_text="hi"),
            ExecutionStore(),
        )
    assert result.code == "paste_unauthorized"
    assert followup.await_count == 0


@pytest.mark.asyncio
async def test_self_supersession_refuses_paste() -> None:
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={"state": "current", "registration_id": "same-reg"},
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="same-reg",
                caller_registration_id="same-reg",
                prompt_text="hi",
            ),
            ExecutionStore(),
        )
    assert result.code == "self_supersession"
    assert followup.await_count == 0


@pytest.mark.asyncio
async def test_idempotent_replay() -> None:
    store = ExecutionStore()
    req = PasteRequest(
        registration_id="target-reg",
        prompt_text="hi",
        grant="explicit",
        idempotency_key="key-1",
    )
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="target-reg",
    )
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={"state": "current", "registration_id": "target-reg"},
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
    ):
        first = await execute_paste(req, store)
        second = await execute_paste(req, store)
    assert first.replayed is False
    assert second.replayed is True
    assert followup.await_count == 1
