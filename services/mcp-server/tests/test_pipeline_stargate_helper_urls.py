"""MCP pipeline helpers must call the canonical Stargate resource paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from tools.pipeline import (
    _pipeline_async,
    _pipeline_cancel,
    _pipeline_result,
    _pipeline_stats,
)

pytestmark = pytest.mark.offline


def _sync_ctx(**methods: MagicMock) -> MagicMock:
    client = MagicMock()
    for name, mock in methods.items():
        setattr(client, name, mock)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def test_pipeline_async_lists_then_posts_capability_url() -> None:
    list_resp = MagicMock()
    list_resp.status_code = 200
    list_resp.json.return_value = {
        "url": "/api/v1/capabilities/demo/demo-pipe",
    }
    dispatch_resp = MagicMock()
    dispatch_resp.status_code = 202
    dispatch_resp.json.return_value = {"execution_id": "exec-1"}
    client = _sync_ctx(get=MagicMock(return_value=list_resp), post=MagicMock(return_value=dispatch_resp))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        out = _pipeline_async("demo-pipe", [{"role": "user", "content": "hi"}], None, None, None)
    assert out["execution_id"] == "exec-1"
    client.get.assert_called_once_with("/api/v1/capabilities", params={"id": "demo-pipe"})
    client.post.assert_called_once()
    assert client.post.call_args.args[0] == "/api/v1/capabilities/demo/demo-pipe"


def test_pipeline_stats_hits_executions_stats() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"running": 0}
    response.raise_for_status = MagicMock()
    client = _sync_ctx(get=MagicMock(return_value=response))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        out = _pipeline_stats()
    assert out == {"running": 0}
    client.get.assert_called_once_with("/api/v1/executions/stats")


def test_pipeline_result_hits_execution_resource() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"execution_id": "exec-9", "status": "completed"}
    client = _sync_ctx(get=MagicMock(return_value=response))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        out = _pipeline_result("exec-9", wait_seconds=0.0)
    assert out["status"] == "completed"
    client.get.assert_called_once_with(
        "/api/v1/executions/exec-9",
        params={"wait": 0.0},
    )


def test_pipeline_cancel_stargate_path_deletes_execution_resource(monkeypatch) -> None:
    monkeypatch.setenv("GIT_INTEGRATION_WORKER_URL", "http://worker:8091")
    giw_resp = MagicMock()
    giw_resp.status_code = 404
    giw_resp.content = b""
    giw_client = MagicMock()
    giw_client.delete.return_value = giw_resp
    giw_client.__enter__ = MagicMock(return_value=giw_client)
    giw_client.__exit__ = MagicMock(return_value=False)
    stargate_resp = MagicMock()
    stargate_resp.status_code = 200
    stargate_resp.json.return_value = {"execution_id": "exec-del", "status": "cancelled"}
    stargate_resp.raise_for_status = MagicMock()
    stargate_client = _sync_ctx(delete=MagicMock(return_value=stargate_resp))
    with patch("tools.pipeline.httpx.Client", return_value=giw_client):
        with patch("tools.pipeline.make_sync_client", return_value=stargate_client):
            out = _pipeline_cancel("exec-del")
    assert out["status"] == "cancelled"
    stargate_client.delete.assert_called_once_with("/api/v1/executions/exec-del")
