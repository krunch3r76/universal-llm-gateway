"""AC6: MCP pipeline cancel relays GIW SDK dispatch ids."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.pipeline import _giw_cancel_dispatch, _pipeline_cancel


def test_giw_cancel_returns_body_on_success(monkeypatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://worker:8091")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"status":"cancelled"}'
    mock_resp.json.return_value = {"status": "cancelled"}
    mock_client = MagicMock()
    mock_client.delete.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    with patch("tools.pipeline.httpx.Client", return_value=mock_client):
        result = _giw_cancel_dispatch("sdk-dispatch-1")
    assert result == {"status": "cancelled"}
    mock_client.delete.assert_called_once()
    assert "sdk-dispatch-1" in mock_client.delete.call_args.args[0]


def test_giw_cancel_404_falls_through_to_stargate(monkeypatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://worker:8091")
    giw_resp = MagicMock()
    giw_resp.status_code = 404
    giw_resp.content = b""
    mock_client = MagicMock()
    mock_client.delete.return_value = giw_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    stargate_resp = MagicMock()
    stargate_resp.status_code = 200
    stargate_resp.json.return_value = {"execution_id": "pipe-1", "status": "cancelled"}
    stargate_resp.raise_for_status = MagicMock()
    with patch("tools.pipeline.httpx.Client", return_value=mock_client):
        with patch("tools.pipeline.make_sync_client") as mock_sync:
            sync_ctx = MagicMock()
            sync_ctx.delete.return_value = stargate_resp
            sync_ctx.__enter__ = MagicMock(return_value=sync_ctx)
            sync_ctx.__exit__ = MagicMock(return_value=False)
            mock_sync.return_value = sync_ctx
            result = _pipeline_cancel("pipe-1")
    assert result == {"execution_id": "pipe-1", "status": "cancelled"}


def test_pipeline_cancel_relays_sdk_dispatch_id(monkeypatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://worker:8091")
    with patch(
        "tools.pipeline._giw_cancel_dispatch",
        return_value={"dispatch_id": "sdk-1", "status": "cancelled"},
    ) as giw_mock:
        result = _pipeline_cancel("sdk-1")
    giw_mock.assert_called_once_with("sdk-1")
    assert result["status"] == "cancelled"
