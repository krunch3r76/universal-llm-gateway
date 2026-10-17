"""Predecessor tape-seal client path and failure events."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_post_session_close_posts_sessions_close_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live Cortex close uses make_sync_client and the relative path."""
    from cdp_ask.cse_session_tape_seal import _post_session_close

    captured: dict[str, object] = {}

    class _Client:
        def post(self, path: str, json: dict | None = None) -> MagicMock:
            captured["path"] = path
            captured["json"] = json
            response = MagicMock()
            response.status_code = 200
            return response

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def _factory(url: str, timeout: float = 0.0) -> _Client:
        captured["url"] = url
        captured["timeout"] = timeout
        return _Client()

    monkeypatch.setattr("transport_utils.make_sync_client", _factory)
    payload = {"session_id": "web-anthropic-test"}
    _post_session_close(payload)
    assert captured["path"] == "/sessions/close"
    assert captured["json"] is payload


def _listening_row() -> dict:
    return {
        "port": 9333,
        "chat_url": "https://claude.ai/cowork/cse_pred_39528",
        "parent_thread": "12286",
        "started_at": 1_792_224_619.0,
    }


@pytest.mark.asyncio
async def test_seal_exception_emits_failed_event_before_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scrape failure emits cdp.tape.seal.failed, then raises the 409 code."""
    from universal_protocol.errors import ProtocolError

    from cdp_ask.cse_session_tape_seal import seal_predecessor_cse_to_tape

    events: list = []
    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal.emit",
        lambda event: events.append(event),
    )
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"rid-39528": _listening_row()},
    )

    async def _boom(_port: int, _url: str) -> list[dict]:
        raise RuntimeError("scrape down")

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _boom,
    )
    with pytest.raises(ProtocolError) as exc_info:
        await seal_predecessor_cse_to_tape("rid-39528")
    assert exc_info.value.code == "standdown.tape_seal_failed"
    assert events[0].signal == "cdp.tape.seal.failed"
    assert events[0].payload["stage"] == "close"
    assert events[0].payload["detail"] == "scrape down"


@pytest.mark.asyncio
async def test_close_body_parse_failure_emits_before_falling_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON 200 body emits cdp.tape.seal.failed, then refuses detach."""
    from universal_protocol.errors import ProtocolError

    from cdp_ask.cse_session_tape_seal import seal_predecessor_cse_to_tape

    events: list = []
    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal.emit",
        lambda event: events.append(event),
    )
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: True)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"rid-39528": _listening_row()},
    )

    async def _scrape(_port: int, _url: str) -> list[dict]:
        return [
            {"author": "user", "text": "keep the predecessor reasoning", "ordinal": 1}
        ]

    def _post(_payload: dict) -> MagicMock:
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        return response

    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal._scrape_predecessor_turns",
        _scrape,
    )
    monkeypatch.setattr("cdp_ask.cse_session_tape_seal._post_session_close", _post)
    monkeypatch.setattr(
        "chat_harvest.archive.archive_chat_transcript",
        lambda **_k: ("cortex://x", "sha", "new"),
    )
    with pytest.raises(ProtocolError) as exc_info:
        await seal_predecessor_cse_to_tape("rid-39528")
    assert exc_info.value.code == "standdown.tape_seal_failed"
    assert events[0].signal == "cdp.tape.seal.failed"
    assert events[0].payload["stage"] == "response_json"
    assert events[0].payload["error_type"] == "ValueError"
