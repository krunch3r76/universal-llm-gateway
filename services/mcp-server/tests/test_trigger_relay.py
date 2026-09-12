"""AC8: MCP trigger relay mirrors project_ask thin httpx pattern."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.trigger import _relay, _schedule_body


def test_relay_posts_schedule_with_bearer(monkeypatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://worker:8091")
    monkeypatch.setenv("AGENT_BUS_TOKEN", "tok-123")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"id":"trig-1"}'
    mock_resp.json.return_value = {"id": "trig-1"}
    mock_client = MagicMock()
    mock_client.request.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    with patch("tools.trigger.httpx.Client", return_value=mock_client):
        result = _relay("POST", "", json_body={"delay_s": 30, "prompt_text": "x"})
    assert result == {"id": "trig-1"}
    call_kwargs = mock_client.request.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer tok-123"
    assert call_kwargs["json"]["prompt_text"] == "x"


def test_schedule_body_includes_recur_every_s_when_set() -> None:
    body = _schedule_body(
        delay_s=30,
        prompt_uri="cortex://notes/system/threads/x.md",
        recur_every_s=14400,
    )
    assert body["recur_every_s"] == 14400


def test_schedule_body_omits_recur_every_s_when_unset() -> None:
    body = _schedule_body(
        delay_s=30,
        prompt_uri="cortex://notes/system/threads/x.md",
    )
    assert "recur_every_s" not in body


def test_schedule_body_includes_optional_relay_fields_when_set() -> None:
    body = _schedule_body(
        delay_s=10,
        prompt_text="wake",
        require_act_receipt=0,
        charter_root="10479",
    )
    assert body["require_act_receipt"] == 0
    assert body["charter_root"] == "10479"
