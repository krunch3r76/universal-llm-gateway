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


def test_close_url_uses_uds_when_socket_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Hub cdp_ask keeps the local cortex-api socket when it is present."""
    from cdp_ask.cse_session_tape_seal import cortex_session_close_url

    sock = tmp_path / "cortex-api.sock"
    sock.write_text("")
    monkeypatch.delenv("CORTEX_API_HTTP", raising=False)
    monkeypatch.setenv("CORTEX_API_SOCK", str(sock))
    assert cortex_session_close_url() == f"unix://{sock}"


def test_close_url_http_when_socket_missing_on_satellite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Jupiter cdp_ask has no hub UDS; EVENTS_INGEST_TCP host is the HTTP target."""
    from cdp_ask.cse_session_tape_seal import cortex_session_close_url

    monkeypatch.delenv("CORTEX_API_HTTP", raising=False)
    monkeypatch.setenv("CORTEX_API_SOCK", "/tmp/does-not-exist-cortex-api.sock")
    monkeypatch.setenv("EVENTS_INGEST_TCP", "10.0.0.67:7101")
    monkeypatch.delenv("CORTEX_API_HTTP_PORT", raising=False)
    assert cortex_session_close_url() == "http://10.0.0.67:8202"


def test_close_url_explicit_http_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """CORTEX_API_HTTP is the remote-start contract and beats a local socket."""
    from cdp_ask.cse_session_tape_seal import cortex_session_close_url

    sock = tmp_path / "cortex-api.sock"
    sock.write_text("")
    monkeypatch.setenv("CORTEX_API_SOCK", str(sock))
    monkeypatch.setenv("CORTEX_API_HTTP", "9.9.9.9:8202")
    assert cortex_session_close_url() == "http://9.9.9.9:8202"


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
    assert len(events) == 1


@pytest.mark.asyncio
async def test_cortex_non_200_emits_failed_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cortex non-200 emits one cdp.tape.seal.failed, then raises."""
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
        response.status_code = 503
        response.json.return_value = {"error": "unavailable"}
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
    assert exc_info.value.data["status_code"] == 503
    assert len(events) == 1
    assert events[0].signal == "cdp.tape.seal.failed"
    assert events[0].payload["stage"] == "cortex_status"
    assert "503" in events[0].payload["detail"]


@pytest.mark.asyncio
async def test_listener_down_emits_degraded_and_returns_same_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead port emits one degraded event and returns the listener_down dict."""
    from cdp_ask.cse_session_tape_seal import seal_predecessor_cse_to_tape

    events: list = []
    monkeypatch.setattr(
        "cdp_ask.cse_session_tape_seal.emit",
        lambda event: events.append(event),
    )
    monkeypatch.setattr("claude_bundles.cdp_lane.is_listening", lambda _port: False)
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_store.load_active",
        lambda: {"rid-39528": _listening_row()},
    )
    result = await seal_predecessor_cse_to_tape("rid-39528")
    assert result == {
        "degraded": True,
        "registration_id": "rid-39528",
        "transcript_path": None,
        "reason": "listener_down",
    }
    assert len(events) == 1
    assert events[0].signal == "cdp.tape.seal.degraded"
    assert events[0].payload["reason"] == "listener_down"
    assert events[0].payload["stage"] == "listener_down"
