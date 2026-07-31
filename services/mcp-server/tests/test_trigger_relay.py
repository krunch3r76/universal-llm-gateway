"""AC8: MCP trigger relay mirrors project_ask thin httpx pattern."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.trigger import _relay


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
