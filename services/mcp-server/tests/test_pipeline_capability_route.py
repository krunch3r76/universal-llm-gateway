"""Slash pipeline ids POST the capability relay; result GETs the run href."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from request_profile import bind_request
from tools.pipeline import (
    _capability_async,
    _pipeline_result,
    register_pipeline_tools,
)

pytestmark = pytest.mark.offline


def _sync_ctx(**methods: MagicMock) -> MagicMock:
    client = MagicMock()
    for name, mock in methods.items():
        setattr(client, name, mock)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def test_capability_async_posts_member_path() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {"location": "/api/v1/capabilities/jobs/bus-reply-watch/runs/r1"}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with bind_request("default", surface="code"):
        with patch("tools.pipeline.make_sync_client", return_value=client):
            out = _capability_async("jobs/bus-reply-watch", {"args": {"thread": "1"}})
    assert out["href"] == "/api/v1/capabilities/jobs/bus-reply-watch/runs/r1"
    client.get.assert_not_called()
    client.post.assert_called_once_with(
        "/api/v1/capabilities/jobs/bus-reply-watch",
        json={"args": {"thread": "1"}, "output_contract": "inline"},
        headers={"X-ULG-Surface": "code"},
    )


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (404, {"code": "job_not_found", "message": "Unknown job nope"}),
        (422, {"code": "unknown_category", "message": "no such category"}),
    ],
)
def test_capability_async_passes_error_body(status_code: int, body: dict) -> None:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        out = _capability_async(
            "jobs/nope" if status_code == 404 else "nope/member", None
        )
    assert out["code"] == body["code"]
    assert out["status_code"] == status_code
    client.get.assert_not_called()


def test_pipeline_result_gets_capability_href_with_int_wait() -> None:
    href = "/api/v1/capabilities/jobs/bus-reply-watch/runs/r1"
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"run_id": "r1", "status": "completed"}
    client = _sync_ctx(get=MagicMock(return_value=resp))
    with bind_request("default", surface="code"):
        with patch("tools.pipeline.make_sync_client", return_value=client):
            out = _pipeline_result(href, 60.0)
    assert out["status"] == "completed"
    client.get.assert_called_once_with(
        href,
        params={"wait": 60},
        headers={"X-ULG-Surface": "code"},
    )


@pytest.mark.asyncio
async def test_tool_routes_slash_id_without_messages() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {"location": "/api/v1/capabilities/jobs/bus-reply-watch/runs/r1"}
    client_http = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    mcp = FastMCP("cap-route")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline.make_sync_client", return_value=client_http):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "pipeline",
                {"op": "async", "pipeline_id": "jobs/bus-reply-watch"},
            )
    body = result.structured_content
    assert body["href"] == "/api/v1/capabilities/jobs/bus-reply-watch/runs/r1"
    client_http.get.assert_not_called()
    client_http.post.assert_called_once()
    assert client_http.post.call_args.args[0] == (
        "/api/v1/capabilities/jobs/bus-reply-watch"
    )


def test_capability_async_posts_writing_member_path() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {
        "location": "/api/v1/capabilities/writing/writer-specialist-v1/runs/r1"
    }
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with bind_request("default", surface="code"):
        with patch("tools.pipeline.make_sync_client", return_value=client):
            out = _capability_async(
                "writing/writer-specialist-v1",
                {"model": "writer-specialist-v1", "args": {}},
            )
    assert out["href"] == "/api/v1/capabilities/writing/writer-specialist-v1/runs/r1"
    client.get.assert_not_called()
    client.post.assert_called_once_with(
        "/api/v1/capabilities/writing/writer-specialist-v1",
        json={
            "model": "writer-specialist-v1",
            "messages": [],
            "pipeline_options": {},
            "output_contract": "inline",
        },
        headers={"X-ULG-Surface": "code"},
    )
    assert "args" not in client.post.call_args.kwargs["json"]


def test_capability_async_flat_options_derive_model() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {"brief": {"signer": "Ada"}},
        )
    body = client.post.call_args.kwargs["json"]
    assert body["model"] == "writer-specialist-v1"
    assert body["pipeline_options"] == {"brief": {"signer": "Ada"}}
    assert "args" not in body


def test_capability_async_passes_messages() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    messages = [{"role": "user", "content": "draft"}]
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async("writing/writer-specialist-v1", {"model": "m"}, messages)
    body = client.post.call_args.kwargs["json"]
    assert body["messages"] == messages
    assert body["model"] == "m"


@pytest.mark.asyncio
async def test_tool_routes_writing_member_to_local_body() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client_http = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    mcp = FastMCP("writing-route")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    messages = [{"role": "user", "content": "hi"}]
    with patch("tools.pipeline.make_sync_client", return_value=client_http):
        async with Client(mcp) as client:
            await client.call_tool(
                "pipeline",
                {
                    "op": "async",
                    "pipeline_id": "writing/writer-specialist-v1",
                    "messages": messages,
                    "options": {"model": "writer-specialist-v1"},
                },
            )
    body = client_http.post.call_args.kwargs["json"]
    assert body["model"] == "writer-specialist-v1"
    assert body["messages"] == messages
    assert "args" not in body
    assert client_http.post.call_args.args[0] == (
        "/api/v1/capabilities/writing/writer-specialist-v1"
    )
