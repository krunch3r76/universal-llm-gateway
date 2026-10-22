"""MCP outbound_write tool registration and intake parity."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastmcp import Client, FastMCP
from tools.pipeline import register_pipeline_tools
from writing_intake.schema import OutboundWriteIntake, validate_intake_payload

pytestmark = pytest.mark.offline


def test_intake_schema_mcp_and_pipeline_assemble_parity() -> None:
    payload = {
        "verbatim_notes": ["Fact A"],
        "recipient": "Ada",
        "objective": "Status",
        "channel": "email",
    }
    intake, err = validate_intake_payload({"intake": payload}, messages=[])
    assert err is None
    assert isinstance(intake, OutboundWriteIntake)
    intake2, err2 = validate_intake_payload(payload, messages=[])
    assert err2 is None
    assert intake.model_dump() == intake2.model_dump()
    _, refused = validate_intake_payload(payload, messages=[{"role": "user", "content": "hi"}])
    assert refused is not None
    assert refused["refused"] == "raw_prompt_prohibited_use_intake_schema"


@pytest.mark.asyncio
async def test_outbound_write_tool_schema_and_relay() -> None:
    with patch("tools.pipeline._pipeline_run") as run:
        run.return_value = {"ok": True}
        with patch("tools.pipeline._refresh_pipeline_timeouts"):
            mcp = FastMCP("outbound-write-test")
            register_pipeline_tools(mcp)
        tools = await mcp.list_tools()
        names = {tool.name for tool in tools}
        assert "outbound_write" in names
        outbound = next(tool for tool in tools if tool.name == "outbound_write")
        schema = getattr(outbound, "parameters", None) or getattr(outbound, "input_schema", None)
        props: set[str] = set()
        if isinstance(schema, dict):
            props = set(schema.get("properties", {}).keys())
            assert "verbatim_notes" in props
        assert "prompt" not in props
        assert "messages" not in props
        async with Client(mcp) as client:
            result = await client.call_tool(
                "outbound_write",
                {
                    "verbatim_notes": ["Hello"],
                    "recipient": "Bob",
                    "objective": "Notify",
                    "channel": "email",
                },
            )
        body = result.structured_content or {}
        assert body.get("ok") is True
        run.assert_called_once()
        kwargs = run.call_args.kwargs
        assert kwargs["options"]["intake"]["recipient"] == "Bob"
        assert kwargs["messages"] == []


@pytest.mark.asyncio
async def test_outbound_write_async_capability_relay() -> None:
    with patch("tools.pipeline._capability_async") as async_call:
        async_call.return_value = {
            "href": "/api/v1/capabilities/writing/writer-specialist-v1/runs/x"
        }
        with patch("tools.pipeline._refresh_pipeline_timeouts"):
            mcp = FastMCP("outbound-write-async")
            register_pipeline_tools(mcp)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "outbound_write",
                {
                    "verbatim_notes": ["Hello"],
                    "recipient": "Bob",
                    "objective": "Notify",
                    "channel": "sms",
                    "async_exec": True,
                },
            )
        body = result.structured_content or {}
        assert "writer-specialist-v1" in str(body.get("href") or body)
        async_call.assert_called_once()


@pytest.mark.asyncio
async def test_outbound_write_async_intake_only_no_messages() -> None:
    with patch("tools.pipeline._capability_async") as async_call:
        async_call.return_value = {"href": "/api/v1/capabilities/writing/writer-specialist-v1/runs/y"}
        with patch("tools.pipeline._refresh_pipeline_timeouts"):
            mcp = FastMCP("outbound-write-async-intake")
            register_pipeline_tools(mcp)
        async with Client(mcp) as client:
            result = await client.call_tool(
                "outbound_write",
                {
                    "recipient": "Clerk",
                    "objective": "Notify",
                    "channel": "email",
                    "verbatim_notes": ["Fact"],
                    "async_exec": True,
                },
            )
        assert (result.structured_content or {}).get("href")
        call = async_call.call_args
        kwargs = call.kwargs if call.kwargs else {}
        args = call.args if call.args else ()
        options = kwargs.get("options") or (args[1] if len(args) > 1 else {})
        messages = kwargs.get("messages") if "messages" in kwargs else (args[2] if len(args) > 2 else None)
        assert messages == [] or messages is None
        assert options["intake"]["recipient"] == "Clerk"
