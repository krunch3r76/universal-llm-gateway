"""Detach response maps tape_seal from seal_predecessor_cse_to_tape (friction 40105)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from universal_protocol.errors import ProtocolError


def _client(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from cdp_ask.app import create_app

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.detach",
        MagicMock(return_value={"registration_id": "rid-detach"}),
    )
    return TestClient(create_app())


@pytest.mark.parametrize(
    ("seal_return", "seal_exc", "expected"),
    [
        (
            {
                "registration_id": "rid-detach",
                "transcript_path": "cortex://tape/x",
                "journal_row_id": "j1",
            },
            None,
            "ok",
        ),
        (
            {
                "degraded": True,
                "registration_id": "rid-detach",
                "transcript_path": None,
                "reason": "listener_down",
            },
            None,
            "skipped_listener_down",
        ),
        (
            {"degraded": True, "registration_id": "rid-detach", "reason": "missing_row"},
            None,
            "degraded",
        ),
        (
            None,
            ProtocolError(
                code="standdown.tape_seal_failed",
                message="seal blew up",
                source="rpc",
                retryable=True,
                data={},
            ),
            "failed",
        ),
    ],
)
def test_detach_tape_seal_mapping(
    monkeypatch: pytest.MonkeyPatch,
    seal_return: dict | None,
    seal_exc: ProtocolError | None,
    expected: str,
) -> None:
    async def _seal(_rid: str, *, store=None) -> dict:
        if seal_exc is not None:
            raise seal_exc
        assert seal_return is not None
        return seal_return

    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest.seal_predecessor_cse_to_tape",
        _seal,
    )
    client = _client(monkeypatch)
    response = client.post(
        "/v1/cse-session/detach",
        json={"registration_id": "rid-detach", "reason": "hop_detach"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("tape_seal") == expected
    if expected == "failed":
        assert "tape_seal_detail" in body


def test_detach_tape_seal_ok_when_cortex_close_returns_201(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detach maps tape_seal=ok when the real seal path accepts Cortex 201 Created."""
    row = {
        "port": 9333,
        "chat_url": "https://claude.ai/cowork/cse_pred_detach",
        "parent_thread": "12286",
        "started_at": 1_792_224_619.0,
    }

    def _post(_payload: dict) -> MagicMock:
        response = MagicMock()
        response.status_code = 201
        response.json.return_value = {
            "journal_row_id": "j-201",
            "transcript_path": "cortex://tape/detach-201.md",
        }
        return response

    monkeypatch.setattr("cdp_ask.cse_session_tape_seal._post_session_close", _post)
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"rid-detach": row},
    )

    async def _scrape(_port: int, _url: str) -> list[dict]:
        return [{"author": "user", "text": "predecessor turn", "ordinal": 1}]

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _scrape,
    )
    monkeypatch.setattr(
        "chat_harvest.archive.archive_chat_transcript",
        lambda **_k: ("cortex://x", "sha", "new"),
    )
    client = _client(monkeypatch)
    response = client.post(
        "/v1/cse-session/detach",
        json={"registration_id": "rid-detach", "reason": "hop_detach"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("tape_seal") == "ok"


def test_detach_tape_seal_failed_on_seal_protocol_error_before_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _seal(_rid: str, *, store=None) -> dict:
        raise ProtocolError(
            code="standdown.missing",
            message="no row",
            source="rpc",
            retryable=False,
            data={},
        )

    monkeypatch.setattr(
        "cdp_ask.cse_session_harvest.seal_predecessor_cse_to_tape",
        _seal,
    )
    detach = MagicMock()
    monkeypatch.setattr("claude_bundles.cdp_registry.detach", detach)
    client = _client(monkeypatch)
    response = client.post(
        "/v1/cse-session/detach",
        json={"registration_id": "rid-detach", "reason": "hop_detach"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body.get("tape_seal") == "failed"
    assert body.get("tape_seal_detail")
    detach.assert_not_called()
