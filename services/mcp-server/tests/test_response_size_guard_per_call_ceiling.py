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
from mcp.types import CallToolRequestParams

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from request_profile import bind_profile, bind_request
from response_overflow_manifest import build_markdown_overflow_manifest
from response_size_guard import (
    ResponseSizeGuard,
    _CURSOR_THRESHOLD,
    _DEFAULT_THRESHOLD,
    _PER_CALL_FLOOR_BYTES,
    _measure_result,
    _reasoning_target_bytes,
    _replacement_result,
    _store,
    _store_result,
    _try_window_agent_bus_result,
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
    request_meta: dict | None = None,
) -> ToolResult:
    _clear_store()
    ctx = MagicMock()
    ctx.message = CallToolRequestParams(
        name=tool_name,
        arguments=arguments or {},
    )

    async def call_next(_ctx: object) -> ToolResult:
        return result

    bind = bind_request(profile, **(request_meta or {})) if request_meta else bind_profile(profile)
    with bind:
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
    asyncio.run(_guard("fs_read", payload, arguments={}))
    assert not [e for e in events if e[0] == "mcp.response.guarded"]


def test_per_call_ceiling_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)
    payload = _fs_result(40 * 1024)
    asyncio.run(
        _guard(
            "fs_read",
            payload,
            arguments={"max_response_bytes": 1_000_000},
            profile="cursor_safe",
        )
    )
    guarded = [e for e in events if e[0] == "mcp.response.guarded"]
    assert guarded
    assert guarded[-1][1]["ceiling_bytes"] == _CURSOR_THRESHOLD
    assert guarded[-1][1]["ceiling_source"] == "per_call"


def test_per_call_ceiling_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)
    payload = _fs_result(50 * 1024)
    events.clear()
    asyncio.run(_guard("fs_read", payload, arguments={"max_response_bytes": 10}))
    guarded = [e for e in events if e[0] == "mcp.response.guarded" and e[1].get("ceiling_source") == "per_call"]
    assert guarded and guarded[0][1]["ceiling_bytes"] == _PER_CALL_FLOOR_BYTES
    for bad in (0, -1, "nope", 3.5, True):
        events.clear()
        asyncio.run(_guard("fs_read", payload, arguments={"max_response_bytes": bad}))
        assert not [e for e in events if e[1].get("ceiling_source") == "per_call"]


def test_fast_path_uses_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict]] = []
    measure_calls: list[int] = []

    def _record(signal: str, **kwargs: object) -> None:
        events.append((signal, dict(kwargs)))

    real_measure = _measure_result

    def spy(result: ToolResult) -> int:
        measure_calls.append(1)
        return real_measure(result)

    monkeypatch.setattr("response_size_guard.record", _record)
    monkeypatch.setattr("response_size_guard._measure_result", spy)
    body_len = 2800
    size = 0
    payload = ToolResult(structured_content={"content": "", "path": "x"})
    while body_len < 20_000:
        body = "q" * body_len
        payload = ToolResult(structured_content={"content": body, "path": "x"})
        size = real_measure(payload)
        if 5888 <= size <= 6400:
            break
        body_len += 64
    assert 5888 <= size <= 6400
    out = asyncio.run(
        _guard("fs_read", payload, arguments={"max_response_bytes": 8192})
    )
    assert measure_calls == [1]
    assert not [e for e in events if e[0] == "mcp.response.guarded"]
    assert out.structured_content is not None or size <= 8192


def test_small_ceiling_keeps_section_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)
    original = _markdown_result(30_000)
    out = asyncio.run(
        _guard(
            "fs_read",
            original,
            arguments={"max_response_bytes": 4096},
        )
    )
    manifest = out.structured_content
    assert isinstance(manifest, dict)
    assert manifest.get("kind") == "markdown_structure"
    assert int(manifest.get("structure_rows", 0)) >= 1
    assert _measure_result(out) <= 4096


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
    ref_id = _store_result("fs_read", original, _measure_result(original))
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
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def slice_worker() -> None:
        try:
            barrier.wait()
            retrieve(id=ref_id, offset=0, limit=3)
        except BaseException as exc:
            errors.append(exc)

    popper = threading.Thread(target=lambda: (barrier.wait(), retrieve(id=ref_id)))
    slices = [threading.Thread(target=slice_worker) for _ in range(3)]
    for thread in slices + [popper]:
        thread.start()
    for thread in slices + [popper]:
        thread.join()
    assert not errors
    assert retrieve(id=ref_id, offset=0, limit=1)["error"]  # type: ignore[index]


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


def test_no_knob_agent_bus_window_unchanged_vs_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thresholds: list[int] = []
    original = _try_window_agent_bus_result

    def spy(result: ToolResult, *, threshold: int) -> ToolResult | None:
        thresholds.append(threshold)
        return original(result, threshold=threshold)

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr("response_size_guard._try_window_agent_bus_result", spy)
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    body_each = 2800
    while body_each > 0:
        turns = [
            {
                "thread": "1",
                "turn_number": n,
                "subject": f"t{n}",
                "body": "b" * body_each,
            }
            for n in range(10)
        ]
        payload = ToolResult(structured_content={"turns": turns})
        if _measure_result(payload) <= _reasoning_target_bytes(_DEFAULT_THRESHOLD):
            break
        body_each -= 100
    assert len(turns) == 10
    out = asyncio.run(
        _guard(
            "agent_bus",
            payload,
            request_meta={"agent_bus_tool": "fetch", "agent_bus_last": 10},
        )
    )
    assert out.structured_content
    assert len(out.structured_content["turns"]) == 10
    assert not [e for e in events if e[0] == "mcp.response.guarded"]
    assert thresholds == [_DEFAULT_THRESHOLD]


def test_no_knob_semantic_args_match_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    replacement_thresholds: list[int] = []
    original_replace = _replacement_result

    def spy_replace(
        ref_id: str, tool_name: str, size: int, threshold: int, result: ToolResult
    ) -> ToolResult:
        replacement_thresholds.append(threshold)
        return original_replace(ref_id, tool_name, size, threshold, result)

    monkeypatch.setattr("response_size_guard.record", lambda s, **k: events.append((s, dict(k))))
    monkeypatch.setattr("response_size_guard._replacement_result", spy_replace)
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)

    body = "z" * 50_000
    payload = ToolResult(structured_content={"content": body, "id": "note:x"})
    asyncio.run(_guard("cortex", payload))
    guarded = [e for e in events if e[0] == "mcp.response.guarded"]
    assert guarded
    assert guarded[-1][1]["effective_bytes"] == _reasoning_target_bytes(_DEFAULT_THRESHOLD)
    assert guarded[-1][1]["ceiling_source"] == "profile"
    assert replacement_thresholds[-1] == _DEFAULT_THRESHOLD

    events.clear()
    replacement_thresholds.clear()
    asyncio.run(_guard("cortex", payload, profile="cursor_safe"))
    guarded = [e for e in events if e[0] == "mcp.response.guarded"]
    assert guarded
    assert replacement_thresholds[-1] == _CURSOR_THRESHOLD


def test_per_call_knob_disables_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    measure_calls: list[int] = []
    real = _measure_result

    def spy(result: ToolResult) -> int:
        measure_calls.append(1)
        return real(result)

    monkeypatch.setattr("response_size_guard._measure_result", spy)
    small = ToolResult(structured_content={"content": "tiny", "path": "x"})
    asyncio.run(_guard("fs_read", small, arguments={"max_response_bytes": 2048}))
    assert len(measure_calls) == 1

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    quoted = ToolResult(structured_content={"content": '"' * 8000, "path": "y"})
    asyncio.run(_guard("fs_read", quoted, arguments={"max_response_bytes": 2048}))
    assert [e for e in events if e[0] == "mcp.response.guarded" and e[1].get("ceiling_source") == "per_call"]


def test_per_call_ceiling_ignored_on_non_read_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    big = ToolResult(structured_content={"items": [{"v": "x" * 5000} for _ in range(30)]})

    async def run_pair(tool: str) -> None:
        with_knob = await _guard(tool, big, arguments={"max_response_bytes": 4096})
        events.clear()
        without_knob = await _guard(tool, big, arguments={})
        assert _measure_result(with_knob) == _measure_result(without_knob)
        guarded = [e for e in events if e[0] == "mcp.response.guarded"]
        if guarded:
            assert guarded[-1][1].get("ceiling_source") == "profile"

    asyncio.run(run_pair("pipeline"))
    asyncio.run(run_pair("agent_bus"))


def test_retrieve_selective_refuses_non_content_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "response_size_guard.record",
        lambda signal, **kwargs: events.append((signal, dict(kwargs))),
    )
    payload = ToolResult(
        structured_content={
            "id": "note:big",
            "name": "Big",
            "fields": {"body": "w" * 80_000},
        }
    )
    _clear_store()
    ref_id = _store_result("cortex_read", payload, _measure_result(payload))
    mcp = _register_retrieve()
    retrieve = _retrieve_fn(mcp)
    for out in (
        retrieve(id=ref_id, offset=0, limit=50),
        retrieve(id=ref_id, section="x"),
    ):
        assert isinstance(out, dict)
        assert out.get("selection_basis") == "longest_string_leaf"
        assert "error" in out
    refused = [e for e in events if e[0] == "mcp.response.retrieve_refused"]
    assert len(refused) == 2
    assert ref_id in _store
    bare = retrieve(id=ref_id)
    assert isinstance(bare, ToolResult)


def test_manifest_omits_section_retrieve_for_non_content() -> None:
    nested_md = "# Top\n\n## Inner\n\nbody\n"
    non_content = ToolResult(
        structured_content={"entity": {"markdown": nested_md, "id": "e1"}}
    )
    manifest_nc = build_markdown_overflow_manifest(
        "rs_nc", "cortex_read", 200_000, 4096, non_content, structure_budget=2048
    )
    if manifest_nc:
        opts = manifest_nc.get("selective_options", [])
        assert not any("section=" in o for o in opts)
    text = "# Root\n\n## Section\n\n" + ("c" * 4000) + "\n"
    content = ToolResult(structured_content={"content": text, "path": "x.md"})
    manifest_c = build_markdown_overflow_manifest(
        "rs_c", "fs_read", 200_000, 4096, content, structure_budget=2048
    )
    assert manifest_c is not None
    assert any('section="' in o for o in manifest_c.get("selective_options", []))


def test_retrieve_offset_only_does_not_pop() -> None:
    lines = "\n".join(f"row{i}" for i in range(10)) + "\n"
    original = ToolResult(structured_content={"content": lines})
    _clear_store()
    ref_id = _store_result("fs_read", original, _measure_result(original))
    mcp = _register_retrieve()
    retrieve = _retrieve_fn(mcp)
    out = retrieve(id=ref_id, offset=3)
    assert isinstance(out, dict)
    assert out["content"].startswith("row3")
    assert "row9" in out["content"]
    assert ref_id in _store


def test_floor_ceiling_note_within_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)
    original = _markdown_result(30_000)
    out = asyncio.run(
        _guard("fs_read", original, arguments={"max_response_bytes": 2048})
    )
    assert _measure_result(out) <= 2048
