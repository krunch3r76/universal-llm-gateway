"""Hermetic harvest attach-path and predecessor seal tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_bundles import cdp_registry

from cdp_ask.cse_session_harvest import execute_harvest
from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_envelope import FollowupCandidate


@pytest.mark.asyncio
async def test_execute_harvest_uses_url_picker_on_attached_path() -> None:
    candidate = FollowupCandidate(
        registration_id="reg-x",
        chat_url="https://claude.ai/cowork/cse_x",
        holder="h",
        purpose="ask",
        cdp_url="http://127.0.0.1:9222",
        provenance={"registration_id": "reg-x"},
    )
    lane = cdp_registry.Registration(
        registration_id="reg-x",
        port=9222,
        profile_suffix="x",
        profile=Path("/tmp/p"),
        cdp_url="http://127.0.0.1:9222",
        holder="h",
        purpose="ask",
    )
    fallback = MagicMock()
    picked = MagicMock()
    ctx = MagicMock()
    pw = MagicMock()
    pw.stop = AsyncMock()
    store = ExecutionStore()
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([candidate], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.list_active",
            lambda: [lane],
        ),
        patch(
            "cdp_ask.cse_session_harvest.connect_cdp",
            AsyncMock(return_value=(pw, MagicMock(), ctx, fallback)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.pick_page_for_chat_url",
            AsyncMock(return_value=picked),
        ) as picker,
        patch(
            "cdp_ask.cse_session_harvest.harvest_with_loading_wait",
            AsyncMock(return_value=HarvestResponse(outcome="no_reply_yet")),
        ) as harvest_wait,
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
    ):
        await execute_harvest(
            HarvestRequest(registration_id="reg-x", source="chat"),
            store,
        )
    picker.assert_awaited_once()
    assert harvest_wait.await_args.args[0] is picked


_USER_TURN = (
    "Predecessor reasoning that never reached files. "
    "Seal this Cowork transcript to continuity tape before detach so the "
    "lane can pour it later. The user asked to keep the hop checkpoint."
)


def _closed_row() -> dict:
    return {
        "registration_id": "4f3e647918c64ab18f8c3f9403d24999",
        "port": 9333,
        "profile_suffix": "pred",
        "holder": "pred",
        "status": "active",
        "purpose": "operator-proxy",
        "parent_thread": "12286",
        "seat_lane": "12286",
        "seat_closed_at": 100.0,
        "seat_bound_at": 90.0,
        "started_at": 1_792_224_619.0,
        "chat_url": "https://claude.ai/cowork/cse_pred_39528",
    }


@pytest.mark.asyncio
async def test_typed_ack_seals_to_tape_and_detaches_closed_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typed ACK seals a valid session close, then detaches the closed seat."""
    from cortex_store.models.journals import SessionCloseRequest
    from cortex_store.routes.session_close_validate import validate_session_close

    monkeypatch.setattr(
        "cortex_store.routes.session_close_validate.lookup_sealed_journal",
        lambda _session_id: None,
    )

    from cdp_ask.cse_session_harvest import seal_predecessor_cse_to_tape

    posted: list[dict] = []

    def _post(payload: dict) -> MagicMock:
        posted.append(payload)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "journal_row_id": 42,
            "transcript_path": "notes/system/transcripts/sealed.md",
            "messages_path": "notes/system/seals/sealed.messages.json",
        }
        return resp

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._post_session_close",
        _post,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.is_listening",
        lambda _port: True,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"4f3e647918c64ab18f8c3f9403d24999": _closed_row()},
    )

    async def _scrape(_port: int, _url: str) -> list[dict]:
        return [
            {"author": "user", "text": _USER_TURN, "ordinal": 1},
            {
                "author": "assistant",
                "text": "Acknowledged. TYPE: SEAT_STAND_DOWN_ACK birth 6348c305",
                "ordinal": 2,
            },
        ]

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _scrape,
    )
    monkeypatch.setattr(
        "chat_harvest.archive.archive_chat_transcript",
        lambda **_k: ("cortex://ephemeral/harvests/x", "sha", "new"),
    )
    sealed = await seal_predecessor_cse_to_tape("4f3e647918c64ab18f8c3f9403d24999")
    assert sealed["transcript_path"]
    assert sealed["journal_row_id"] == 42
    body = SessionCloseRequest.model_validate(posted[0])
    validated = validate_session_close(body)
    assert validated.transcript_path
    assert "agent-bus:12286" in (body.entity_ids or [])

    lane = cdp_registry.Registration(
        registration_id="4f3e647918c64ab18f8c3f9403d24999",
        port=9333,
        profile_suffix="pred",
        profile=Path("/tmp/p"),
        cdp_url="http://127.0.0.1:9333",
        holder="pred",
        purpose="operator-proxy",
        parent_thread="12286",
        seat_lane="12286",
        seat_closed_at=100.0,
    )
    candidate = FollowupCandidate(
        registration_id=lane.registration_id,
        chat_url=_closed_row()["chat_url"],
        holder="pred",
        purpose="operator-proxy",
        cdp_url=lane.cdp_url,
        provenance={"registration_id": lane.registration_id},
    )
    pw = MagicMock()
    pw.stop = AsyncMock()
    detach = MagicMock(return_value={"registration_id": lane.registration_id})
    with (
        patch(
            "cdp_ask.cse_session_harvest.discover_candidates",
            AsyncMock(return_value=([candidate], None, None)),
        ),
        patch(
            "cdp_ask.cse_session_harvest.cdp_registry.list_active",
            lambda: [lane],
        ),
        patch(
            "cdp_ask.cse_session_harvest.connect_cdp",
            AsyncMock(return_value=(pw, MagicMock(), MagicMock(), MagicMock())),
        ),
        patch(
            "cdp_ask.cse_session_harvest.harvest_page",
            AsyncMock(
                return_value=HarvestResponse(
                    outcome="harvested",
                    ack_class="typed_ack",
                    chat_url=_closed_row()["chat_url"],
                )
            ),
        ),
        patch("cdp_ask.cse_session_harvest.emit", lambda _event: None),
        patch("cdp_ask.cse_session_harvest.cdp_registry.detach", detach),
        patch(
            "claude_bundles.cdp_registry_store.append_log",
            lambda *_a, **_k: None,
        ),
        patch(
            "claude_bundles.cdp_registry.attachment_journal.fold_attachment_journal",
            lambda *_a, **_k: {},
        ),
    ):
        result = await execute_harvest(
            HarvestRequest(registration_id=lane.registration_id, source="chat"),
            ExecutionStore(),
        )
    assert result.outcome == "harvested"
    detach.assert_called_once()
    assert detach.call_args.kwargs["reason"] == "standdown_ack_harvested"


@pytest.mark.asyncio
async def test_tape_seal_failure_still_detaches_on_live_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cortex 500 on a live port records seal failure but detach proceeds (a:40105)."""
    from fastapi.testclient import TestClient

    from cdp_ask.app import create_app

    def _post(_payload: dict) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "unavailable"
        resp.json.return_value = {"error": "unavailable"}
        return resp

    monkeypatch.setattr("cdp_ask.cse_session_tape_seal._post_session_close", _post)
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"4f3e647918c64ab18f8c3f9403d24999": _closed_row()},
    )

    async def _scrape(_port: int, _url: str) -> list[dict]:
        return [{"author": "user", "text": _USER_TURN, "ordinal": 1}]

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _scrape,
    )
    monkeypatch.setattr(
        "chat_harvest.archive.archive_chat_transcript",
        lambda **_k: ("cortex://x", "sha", "new"),
    )
    detach = MagicMock(
        return_value={"registration_id": "4f3e647918c64ab18f8c3f9403d24999"}
    )
    monkeypatch.setattr("claude_bundles.cdp_registry.detach", detach)
    client = TestClient(create_app())
    response = client.post(
        "/v1/cse-session/detach",
        json={
            "registration_id": "4f3e647918c64ab18f8c3f9403d24999",
            "reason": "hop_detach",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("registration_id") == "4f3e647918c64ab18f8c3f9403d24999"
    assert body.get("tape_seal") == "failed"
    detach.assert_called_once()
