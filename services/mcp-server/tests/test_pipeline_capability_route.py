"""Slash pipeline ids POST the capability relay; result GETs the run href."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from request_profile import bind_request
from tools.pipeline import (
    _CAPABILITY_PREFIX,
    _capability_async,
    _pipeline_list,
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


def test_pipeline_list_category_uses_query_relay_and_forwards_surface() -> None:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "members": [{"name": "install-ecosystem-plugin", "href": "/x"}]
    }
    client = _sync_ctx(get=MagicMock(return_value=resp))
    with bind_request("default", surface="code"):
        with patch("tools.pipeline.make_sync_client", return_value=client):
            out = _pipeline_list("jobs")
    assert out["members"][0]["name"] == "install-ecosystem-plugin"
    client.get.assert_called_once_with(
        "/api/v1/capabilities",
        headers={"X-ULG-Surface": "code"},
        params={"category": "jobs"},
    )


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


@pytest.mark.asyncio
async def test_zero_slash_id_is_not_a_capability_route() -> None:
    mcp = FastMCP("cap-route-plain")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline.make_sync_client") as http:
        with patch("tools.pipeline._capability_async") as capability:
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "pipeline",
                    {"op": "async", "pipeline_id": "jobs"},
                )
    body = result.structured_content
    assert body["error"]["code"] == "missing_required"
    capability.assert_not_called()
    http.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["async", "run"])
@pytest.mark.parametrize("pipeline_id", ["a/b/c", "jobs/../x", "jobs/%2e%2e"])
async def test_malformed_capability_id_rejects_before_http(
    op: str, pipeline_id: str
) -> None:
    mcp = FastMCP("cap-route-bad")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline.make_sync_client") as http:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "pipeline",
                {"op": op, "pipeline_id": pipeline_id},
            )
    body = result.structured_content
    assert body["error"]["code"] == "capability_id_invalid"
    http.assert_not_called()


@pytest.mark.parametrize(
    "href",
    [
        "/api/v1/capabilities/jobs/../x",
        "/api/v1/capabilities/jobs/%2e%2e/x",
        "/api/v1/capabilities/jobs/%2E%2E/x",
        "/api/v1/capabilities/jobs/..\\x",
    ],
)
def test_capability_result_href_dotdot_rejects_before_http(href: str) -> None:
    with patch("tools.pipeline.make_sync_client") as http:
        out = _pipeline_result(href, 0.0)
    assert out["error"]["code"] == "capability_href_invalid"
    http.assert_not_called()


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


def test_capability_async_local_carries_args_into_pipeline_options() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    working_set = [{"pin_id": "P1"}]
    brief = {"signer": "Ada"}
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {
                "args": {"working_set": working_set, "brief": brief},
                "model": "writer-specialist-v1",
            },
        )
    body = client.post.call_args.kwargs["json"]
    assert body == {
        "model": "writer-specialist-v1",
        "messages": [],
        "pipeline_options": {"working_set": working_set, "brief": brief},
        "output_contract": "inline",
    }
    assert "args" not in body


def test_capability_async_local_args_merge_precedence() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {
                "model": "writer-specialist-v1",
                "args": {"brief": {"from": "args"}, "extra": 1},
                "pipeline_options": {"brief": {"from": "explicit"}},
            },
        )
    body = client.post.call_args.kwargs["json"]
    assert body["pipeline_options"] == {
        "brief": {"from": "explicit"},
        "extra": 1,
    }


def _accepted() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    return resp


def test_capability_async_relay_strips_dispatch_thread_id() -> None:
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "jobs/bus-reply-watch",
            {"args": {"thread": "1"}, "dispatch_thread_id": " 15902 "},
        )
    client.post.assert_called_once_with(
        "/api/v1/capabilities/jobs/bus-reply-watch",
        json={
            "args": {"thread": "1"},
            "output_contract": "inline",
            "dispatch_thread_id": "15902",
        },
        headers={},
    )


def test_capability_async_local_model_forwards_dispatch_thread_id() -> None:
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    messages = [{"role": "user", "content": "draft"}]
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {"model": "m", "dispatch_thread_id": "15902"},
            messages,
        )
    body = client.post.call_args.kwargs["json"]
    assert body["dispatch_thread_id"] == "15902"
    assert body["pipeline_options"]["dispatch_thread_id"] == "15902"
    assert body["model"] == "m"
    assert body["messages"] == messages


def test_capability_async_local_pipeline_options_forwards_dispatch_thread_id() -> None:
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {
                "pipeline_options": {"brief": "x"},
                "dispatch_thread_id": "15902",
            },
        )
    body = client.post.call_args.kwargs["json"]
    assert body["dispatch_thread_id"] == "15902"
    assert "dispatch_thread_id" not in body["pipeline_options"]


def test_capability_async_local_flat_keeps_dispatch_thread_id() -> None:
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {"brief": {"signer": "Ada"}, "dispatch_thread_id": "15902"},
        )
    body = client.post.call_args.kwargs["json"]
    assert body["dispatch_thread_id"] == "15902"
    assert body["pipeline_options"]["dispatch_thread_id"] == "15902"
    assert body["pipeline_options"]["brief"] == {"signer": "Ada"}


def test_capability_async_blank_dispatch_thread_id_omitted() -> None:
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "jobs/bus-reply-watch",
            {"args": {"thread": "1"}, "dispatch_thread_id": "   "},
        )
    body = client.post.call_args.kwargs["json"]
    assert "dispatch_thread_id" not in body
    assert body == {"args": {"thread": "1"}, "output_contract": "inline"}


def test_capability_async_nonstr_dispatch_thread_id_omitted() -> None:
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "jobs/bus-reply-watch",
            {"args": {"thread": "1"}, "dispatch_thread_id": 123},
        )
    body = client.post.call_args.kwargs["json"]
    assert "dispatch_thread_id" not in body
    assert body == {"args": {"thread": "1"}, "output_contract": "inline"}


def test_capability_async_writer_flat_dispatch_thread_id_reaches_pipeline_options() -> (
    None
):
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {
                "brief": {"topic": "x"},
                "writer_seat": "cdp",
                "dispatch_thread_id": "15800",
            },
        )
    client.post.assert_called_once()
    assert client.post.call_args.args[0] == (
        f"{_CAPABILITY_PREFIX}writing/writer-specialist-v1"
    )
    body = client.post.call_args.kwargs["json"]
    assert body["model"] == "writer-specialist-v1"
    assert body["pipeline_options"] == {
        "brief": {"topic": "x"},
        "writer_seat": "cdp",
        "dispatch_thread_id": "15800",
    }
    assert body["dispatch_thread_id"] == "15800"


def test_capability_async_local_blank_dispatch_thread_id_stays_in_pipeline_options() -> (
    None
):
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {"brief": {"topic": "x"}, "dispatch_thread_id": "   "},
        )
    body = client.post.call_args.kwargs["json"]
    assert "dispatch_thread_id" not in body
    assert body["pipeline_options"]["dispatch_thread_id"] == "   "
    assert body["pipeline_options"]["brief"] == {"topic": "x"}


def test_capability_async_local_nonstr_dispatch_thread_id_stays_in_pipeline_options() -> (
    None
):
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=_accepted()))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {"brief": {"topic": "x"}, "dispatch_thread_id": 123},
        )
    body = client.post.call_args.kwargs["json"]
    assert "dispatch_thread_id" not in body
    assert body["pipeline_options"]["dispatch_thread_id"] == 123
    assert body["pipeline_options"]["brief"] == {"topic": "x"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pipeline_id",
    ["capability:jobs/install-ecosystem-plugin", "jobs/install-ecosystem-plugin"],
)
async def test_tool_run_posts_jobs_install_capability(pipeline_id: str) -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {
        "location": "/api/v1/capabilities/jobs/install-ecosystem-plugin/runs/r1"
    }
    client_http = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    mcp = FastMCP("cap-run-install")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with bind_request("default", surface="code"):
        with patch("tools.pipeline.make_sync_client", return_value=client_http):
            with patch("tools.pipeline._pipeline_run") as pipeline_run:
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "pipeline",
                        {
                            "op": "run",
                            "pipeline_id": pipeline_id,
                            "options": {"args": {"local_only": True}},
                        },
                    )
    body = result.structured_content
    assert body["href"] == (
        "/api/v1/capabilities/jobs/install-ecosystem-plugin/runs/r1"
    )
    pipeline_run.assert_not_called()
    client_http.post.assert_called_once_with(
        "/api/v1/capabilities/jobs/install-ecosystem-plugin",
        json={"args": {"local_only": True}, "output_contract": "inline"},
        headers={"X-ULG-Surface": "code"},
    )
    assert client_http.post.call_args.args[0] != "/v1/chat/completions"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (403, {"code": "surface_undeclared", "message": "surface not bound"}),
        (422, {"code": "unknown_category", "message": "no such category"}),
    ],
)
async def test_tool_run_passes_capability_error_body(
    status_code: int, body: dict
) -> None:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.headers = {}
    client_http = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    mcp = FastMCP("cap-run-errors")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with bind_request("default", surface="code"):
        with patch("tools.pipeline.make_sync_client", return_value=client_http):
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "pipeline",
                    {
                        "op": "run",
                        "pipeline_id": "jobs/install-ecosystem-plugin",
                        "options": {"args": {}},
                    },
                )
    out = result.structured_content
    assert out["code"] == body["code"]
    assert out["status_code"] == status_code
    client_http.get.assert_not_called()


@pytest.mark.asyncio
async def test_tool_run_zero_slash_id_requires_messages() -> None:
    mcp = FastMCP("cap-run-plain")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline.make_sync_client") as http:
        with patch("tools.pipeline._capability_async") as capability:
            with patch("tools.pipeline._pipeline_run") as pipeline_run:
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "pipeline",
                        {"op": "run", "pipeline_id": "jobs"},
                    )
    body = result.structured_content
    assert body["error"]["code"] == "missing_required"
    capability.assert_not_called()
    pipeline_run.assert_not_called()
    http.assert_not_called()


@pytest.mark.asyncio
async def test_tool_run_zero_slash_id_uses_pipeline_run() -> None:
    mcp = FastMCP("cap-run-chat")
    messages = [{"role": "user", "content": "hi"}]
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline._capability_async") as capability:
        with patch(
            "tools.pipeline._pipeline_run", return_value={"content": "ok"}
        ) as pipeline_run:
            async with Client(mcp) as client:
                await client.call_tool(
                    "pipeline",
                    {"op": "run", "pipeline_id": "jobs", "messages": messages},
                )
    capability.assert_not_called()
    pipeline_run.assert_called_once()
    assert pipeline_run.call_args.args[0] == "jobs"
    assert pipeline_run.call_args.args[1] == messages


def test_writing_slash_without_messages_posts_local_model() -> None:
    """writing/writer-specialist-v1 is a local pipeline, not a satellite relay."""
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async("writing/writer-specialist-v1", None)
    body = client.post.call_args.kwargs["json"]
    assert body["model"] == "writer-specialist-v1"
    assert body["messages"] == []
    assert "args" not in body
    assert client.post.call_args.args[0] == (
        "/api/v1/capabilities/writing/writer-specialist-v1"
    )


def test_writing_slash_model_normalizes_to_bare_member() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    with patch("tools.pipeline.make_sync_client", return_value=client):
        _capability_async(
            "writing/writer-specialist-v1",
            {"model": "writing/writer-specialist-v1"},
        )
    assert client.post.call_args.kwargs["json"]["model"] == "writer-specialist-v1"


@pytest.mark.asyncio
async def test_tool_run_forwards_messages_on_slash_id() -> None:
    mcp = FastMCP("cap-run-slash-messages")
    messages = [{"role": "user", "content": "draft"}]
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline._capability_async", return_value={"run_id": "r"}) as cap:
        async with Client(mcp) as client:
            await client.call_tool(
                "pipeline",
                {
                    "op": "run",
                    "pipeline_id": "writing/writer-specialist-v1",
                    "messages": messages,
                },
            )
    cap.assert_called_once()
    assert cap.call_args.args[0] == "writing/writer-specialist-v1"
    assert cap.call_args.args[2] == messages


@pytest.mark.asyncio
async def test_tool_async_accepts_pipeline_alias() -> None:
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"run_id": "r1"}
    resp.headers = {}
    client_http = _sync_ctx(get=MagicMock(), post=MagicMock(return_value=resp))
    mcp = FastMCP("pipeline-alias")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    with patch("tools.pipeline.make_sync_client", return_value=client_http):
        async with Client(mcp) as client:
            await client.call_tool(
                "pipeline",
                {
                    "op": "async",
                    "pipeline": "writing/writer-specialist-v1",
                },
            )
    body = client_http.post.call_args.kwargs["json"]
    assert body["model"] == "writer-specialist-v1"
    assert client_http.post.call_args.args[0] == (
        "/api/v1/capabilities/writing/writer-specialist-v1"
    )


@pytest.mark.asyncio
async def test_tool_rejects_conflicting_pipeline_alias() -> None:
    mcp = FastMCP("pipeline-alias-conflict")
    with patch("tools.pipeline._refresh_pipeline_timeouts"):
        register_pipeline_tools(mcp)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "pipeline",
            {
                "op": "async",
                "pipeline_id": "writer-specialist-v1",
                "pipeline": "writing/writer-specialist-v1",
            },
        )
    body = result.structured_content
    assert body["error"]["code"] == "conflicting_pipeline_id"
