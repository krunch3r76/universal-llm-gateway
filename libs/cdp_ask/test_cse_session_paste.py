"""Paste authorization, identity refusal, and idempotent replay tests."""

from __future__ import annotations

import json
from pathlib import Path
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


@pytest.mark.asyncio
async def test_hop_pair_request_triple_authorizes_paste() -> None:
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="target-reg",
    )

    def _prov(**kwargs: object) -> dict[str, object]:
        reg = str(kwargs.get("registration_id") or "")
        if reg == "caller-reg":
            return {
                "state": "current",
                "registration_id": "caller-reg",
                "parent_thread_proven": "7437",
            }
        return {
            "state": "current",
            "registration_id": "target-reg",
            "parent_thread_proven": "7437",
        }

    with (
        patch("cdp_ask.cse_session_paste.resolve_provenance", side_effect=_prov),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="target-reg",
                caller_registration_id="caller-reg",
                superseded_registration_id="target-reg",
                parent_thread="7437",
                prompt_text="stand down",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    assert followup.await_count == 1


@pytest.mark.asyncio
async def test_stand_down_authorized_from_hop_watch_without_request_triple(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successor stand_down needs only the target — hop already recorded the pair."""
    watch = tmp_path / "hop_cadence_watches.json"
    watch.write_text(
        json.dumps(
            {
                "7437": {
                    "thread_id": "7437",
                    "superseded_registration_id": "pred-reg",
                    "successor_birth_id": "birth-1",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch))
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="pred-reg",
    )
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={
                "state": "current",
                "registration_id": "pred-reg",
                "parent_thread_proven": "7437",
            },
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="pred-reg",
                envelope="stand_down",
                prompt_text="TYPE: SEAT_STAND_DOWN",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    assert followup.await_count == 1


@pytest.mark.asyncio
async def test_free_envelope_does_not_use_hop_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = tmp_path / "hop_cadence_watches.json"
    watch.write_text(
        json.dumps({"7437": {"superseded_registration_id": "pred-reg"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_AUTO_HOP_WATCHES_PATH", str(watch))
    with (
        patch(
            "cdp_ask.cse_session_paste.resolve_provenance",
            return_value={"state": "current", "registration_id": "pred-reg"},
        ),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(),
        ) as followup,
    ):
        result = await execute_paste(
            PasteRequest(registration_id="pred-reg", prompt_text="hi"),
            ExecutionStore(),
        )
    assert result.code == "paste_unauthorized"
    assert followup.await_count == 0


@pytest.mark.asyncio
async def test_stand_down_same_lane_claimant_without_watch_pair() -> None:
    """Colliding CSEs on one parent_thread may not be the hop-watch predecessor."""
    followup_ok = FollowupProjectAskResponse(
        ok=True,
        send_verified=True,
        receipt="dom_paste",
        registration_id="live-pred",
    )

    def _prov(**kwargs: object) -> dict[str, object]:
        reg = str(kwargs.get("registration_id") or "")
        if reg == "live-succ":
            return {
                "state": "current",
                "registration_id": "live-succ",
                "parent_thread_proven": "7437",
            }
        return {
            "state": "current",
            "registration_id": "live-pred",
            "parent_thread_proven": "7437",
        }

    with (
        patch("cdp_ask.cse_session_paste.resolve_provenance", side_effect=_prov),
        patch(
            "cdp_ask.cse_session_paste.execute_followup",
            AsyncMock(return_value=followup_ok),
        ) as followup,
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={"7437": {"superseded_registration_id": "other-hop-pred"}},
        ),
    ):
        result = await execute_paste(
            PasteRequest(
                registration_id="live-pred",
                caller_registration_id="live-succ",
                envelope="stand_down",
                prompt_text="TYPE: SEAT_STAND_DOWN",
            ),
            ExecutionStore(),
        )
    assert result.ok is True
    assert followup.await_count == 1
