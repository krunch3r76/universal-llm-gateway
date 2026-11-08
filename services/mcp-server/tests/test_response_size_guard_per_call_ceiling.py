"""Per-call max_response_bytes ceiling and non-popping retrieve windows."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from request_profile import bind_profile
from response_overflow_manifest import try_markdown_overflow_replacement
from response_size_guard import (
    ResponseSizeGuard,
    _DEFAULT_THRESHOLD,
    _PER_CALL_FLOOR_BYTES,
    _CURSOR_THRESHOLD,
    _measure_result,
    _store,
    _store_result,
    register_response_guard,
)
from tools._agent_bus_read import register_agent_bus_read_tool
from tools._cortex_read import register_cortex_read_tool
from tools._fs_read import register_fs_read_tool

pytestmark = pytest.mark.offline


def _clear_store() -> None:
    with __import__("response_size_guard", fromlist=["_lock"])._lock:
        _store.clear()


def _fs_result(payload_size: int) -> ToolResult:
    body = "x" * payload_size
    return ToolResult(
        structured_content={"content": body, "path": "data.bin"},
    )


def _markdown_result(body_repeat: int) -> ToolResult:
    text = "# Root\n\n## Section\n\n" + ("y" * body_repeat) + "\n"
    return ToolResult(
        structured_content={"content": text, "path": "notes/x.md"},
    )


async def _guard(
    tool_name: str,
    result: ToolResult,
    *,
    arguments: dict | None = None,
    profile: str = "default",
) -> ToolResult:
    _clear_store()
    ctx = MagicMock()
    ctx.message = CallToolRequestParams(
        name=tool_name,
        arguments=arguments or {},
    )

    async def call_next(_ctx: object) -> ToolResult:
        return result

    with bind_profile(profile):
        return await ResponseSizeGuard().on_call_tool(ctx, call_next)


def test_per_call_ceiling_lowers_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []

    def _record(signal: str, **kwargs: object) -> None:
        events.append((signal, dict(kwargs)))

    monkeypatch.setattr("response_size_guard.record", _record)
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)

    payload = _fs_result(20 * 1024)
    guarded = asyncio.run(
        _guard("fs_read", payload, arguments={"max_response_bytes": 8192})
    )
    assert guarded.structured_content is not None or "retrieve" in str(guarded.content)
    guarded_events = [e for e in events if e[0] == "mcp.response.guarded"]
    assert guarded_events
    assert guarded_events[-1][1]["ceiling_source"] == "per_call"
    assert guarded_events[-1][1]["ceiling_bytes"] == 8192

    events.clear()
    passthrough = asyncio.run(_guard("fs_read", payload, arguments={}))
    assert not [e for e in events if e[0] == "mcp.response.guarded"]


def test_per_call_ceiling_never_raises() -> None:
    payload = _fs_result(40 * 1024)
    out = asyncio.run(
        _guard(
            "fs_read",
            payload,
            arguments={"max_response_bytes": 1_000_000},
            profile="cursor_safe",
        )
    )
    # Under cursor_safe (32KiB) without lowering, 40KiB would guard; raise attempt ignored.
    assert out is not payload
    text = out.content if isinstance(out.content, str) else out.content[0].text
    assert "retrieve" in text or (
        isinstance(out.structured_content, dict)
        and out.structured_content.get("large_payload")
    )


def test_per_call_ceiling_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)
    payload = _fs_result(50 * 1024)
    for bad in (10, 0, -1, "nope", 3.5):
        asyncio.run(_guard("fs_read", payload, arguments={"max_response_bytes": bad}))
    guarded = [e for e in events if e[0] == "mcp.response.guarded" and e[1].get("ceiling_source") == "per_call"]
    assert guarded
    assert guarded[0][1]["ceiling_bytes"] == _PER_CALL_FLOOR_BYTES
    assert not [e for e in events if e[0] == "mcp.response.guarded" and e[1].get("ceiling_bytes") == 10]


def test_fast_path_uses_ceiling() -> None:
    items = {"rows": [{"id": i, "v": "a" * 80} for i in range(120)]}
    payload = ToolResult(structured_content=items)
    estimate = len(json.dumps(items).encode("utf-8"))
    assert estimate > 4096
    out = asyncio.run(
        _guard("pipeline", payload, arguments={"max_response_bytes": 8192})
    )
    assert [k for k in _store]
    assert out.structured_content is None or out.structured_content.get("ref_id")


def test_small_ceiling_keeps_section_tree() -> None:
    original = _markdown_result(30_000)
    size = _measure_result(original)
    _clear_store()
    ref_id = _store_result("fs_read", original, size)
    replacement = try_markdown_overflow_replacement(
        ref_id,
        "fs_read",
        size,
        4096,
        original,
        measure_result=_measure_result,
        structure_budget=2048,
    )
    assert replacement is not None
    manifest = replacement.structured_content
    assert isinstance(manifest, dict)
    assert manifest["kind"] == "markdown_structure"
    assert manifest["structure_rows"] >= 1
    assert _measure_result(replacement) <= 4096


def _register_retrieve() -> FastMCP:
    mcp = FastMCP("retrieve-test")
    register_response_guard(mcp)
    return mcp


def _retrieve_fn(mcp: FastMCP):
    tools = asyncio.run(mcp.list_tools())
    return next(tool.fn for tool in tools if tool.name == "retrieve")


def test_retrieve_section_does_not_pop() -> None:
    text = "# Doc\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta\n"
    original = ToolResult(structured_content={"content": text})
    _clear_store()
    ref_id = _store_result("fs", original, _measure_result(original))
    mcp = _register_retrieve()
    retrieve = _retrieve_fn(mcp)

    section_out = retrieve(id=ref_id, section="Alpha")
    assert isinstance(section_out, dict)
    assert "alpha body" in section_out["content"]
    assert ref_id in _store

    lines_out = retrieve(id=ref_id, offset=0, limit=5)
    assert isinstance(lines_out, dict)
    assert lines_out["total_lines"] >= 5
    assert ref_id in _store

    full = retrieve(id=ref_id)
    assert ref_id not in _store
    assert isinstance(full, ToolResult)

    again = retrieve(id=ref_id)
    assert isinstance(again, dict) and "error" in again


def test_fast_path_unchanged_without_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def spy(result: ToolResult) -> int:
        calls.append(1)
        return len(json.dumps(result.structured_content, default=str).encode("utf-8"))

    monkeypatch.setattr("response_size_guard._measure_result", spy)
    small = ToolResult(structured_content={"a": 1, "b": 2})
    asyncio.run(_guard("pipeline", small, arguments={}))
    assert calls == []


def test_retrieve_after_store_clear_errors() -> None:
    _clear_store()
    mcp = _register_retrieve()
    retrieve = _retrieve_fn(mcp)
    out = retrieve(id="rs_missing")
    assert isinstance(out, dict) and "error" in out


def test_retrieve_slice_pop_race() -> None:
    text = "\n".join(f"line {i}" for i in range(200)) + "\n"
    original = ToolResult(structured_content={"content": text})
    _clear_store()
    ref_id = _store_result("fs", original, _measure_result(original))
    mcp = _register_retrieve()
    retrieve = _retrieve_fn(mcp)
    errors: list[BaseException] = []

    def slice_worker() -> None:
        try:
            retrieve(id=ref_id, offset=0, limit=3)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=slice_worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert ref_id in _store
    popped = retrieve(id=ref_id)
    assert isinstance(popped, ToolResult)
    assert retrieve(id=ref_id, section="")["error"]  # type: ignore[index]


def test_tool_schemas_expose_ceiling_and_retrieve_window() -> None:
    mcp = FastMCP("schema-ceiling")
    register_fs_read_tool(mcp, surface="code")
    register_agent_bus_read_tool(mcp)
    register_cortex_read_tool(mcp, surface="code")
    register_response_guard(mcp)
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name in ("fs_read", "agent_bus_read", "cortex_read"):
        schema = tools[name].parameters
        props = schema.get("properties", schema)
        if isinstance(props, dict):
            assert "max_response_bytes" in props
    retrieve_schema = tools["retrieve"].parameters
    rprops = retrieve_schema.get("properties", retrieve_schema)
    assert isinstance(rprops, dict)
    for key in ("section", "offset", "limit"):
        assert key in rprops


def test_retrieve_survives_primary_prune() -> None:
    from server import _PRIMARY_TOOLS

    assert "retrieve" in _PRIMARY_TOOLS
