"""cortex_brief mint_only MCP wrapper tests."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))

from cortex_store.routes.boot.mint import perform_boot_mint
from request_profile import bind_request
from response_size_guard import ResponseSizeGuard, _measure_result

from tools.cortex_named_tools import _orchestration_tools as orchestration_tools
from tools.cortex_named_tools._boot_runner import BootMode

_FIXED = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)

_FORBIDDEN_KEYS = frozenset(
    {
        "briefing_card",
        "sections_available",
        "injected_artifacts",
        "byte_ledger",
        "skills_index_ref",
    }
)


class _DummyMcp:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, title: str) -> Any:  # noqa: ARG002
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _boot_tool_result(payload: dict[str, Any]) -> ToolResult:
    text = json.dumps(payload, default=str)
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )


async def _guard_cortex_brief(result: ToolResult) -> ToolResult:
    ctx = MagicMock()
    ctx.message = CallToolRequestParams(name="cortex_brief", arguments={})

    async def call_next(_ctx: object) -> ToolResult:
        return result

    with bind_request("default", structured_capable=False):
        return await ResponseSizeGuard().on_call_tool(ctx, call_next)


def test_mint_only_returns_without_briefing_card(monkeypatch: Any) -> None:
    events: list[str] = []

    def _cx(method: str, path: str, body: dict[str, Any] | None = None, **_kw: Any) -> dict:
        assert method == "POST"
        assert path == "/boot-mint"
        assert body and body.get("seat") == "web-anthropic"
        return perform_boot_mint("web-anthropic", at=_FIXED)

    monkeypatch.setattr(orchestration_tools, "cx", _cx)
    monkeypatch.setattr(
        orchestration_tools,
        "record",
        lambda signal, **_kw: events.append(signal),
    )

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="web-anthropic", mint_only=True)

    assert result["mint_only"] is True
    assert _FORBIDDEN_KEYS.isdisjoint(result.keys())
    assert result["session_id"].startswith("web-anthropic-")
    assert "mcp.cortex.boot.mint" in events
    assert "mcp.cortex.boot" not in events


def test_mint_only_companion_param_refused(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        orchestration_tools,
        "cx",
        lambda *_a, **_k: pytest.fail("relay must not run"),
    )
    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](
        seat="web-anthropic",
        mint_only=True,
        views=["plan:foo"],
    )
    assert result["reason"] == "mint_only_companion_params"
    assert "views" in result["missing_fields"]


def test_default_path_still_runs_full_brief(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    def _run(identity: Any, **kwargs: Any) -> dict[str, Any]:
        seen["identity"] = identity
        seen["kwargs"] = kwargs
        return {
            "session_id": "cursor-2026-11-23-080000-abc",
            "briefing_card": "card",
            "sections_available": [],
            "mode": BootMode.LIVE.value,
        }

    monkeypatch.setattr(orchestration_tools, "run_cortex_brief", _run)
    monkeypatch.setattr(
        orchestration_tools,
        "cx",
        lambda *_a, **_k: pytest.fail("mint relay must not run on default path"),
    )

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat="cursor")

    assert result["briefing_card"] == "card"
    assert "sections_available" in result
    assert seen["kwargs"]["domain"] is None


def test_fetch_raise_mint_only_ok_default_fails(monkeypatch: Any) -> None:
    def _cx(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return perform_boot_mint("web-anthropic", at=_FIXED)

    def _run_raise(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise RuntimeError("fetch layer failed")

    monkeypatch.setattr(orchestration_tools, "cx", _cx)
    monkeypatch.setattr(orchestration_tools, "run_cortex_brief", _run_raise)
    monkeypatch.setattr(orchestration_tools, "record", lambda *_a, **_k: None)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    mint = mcp.tools["cortex_brief"](seat="web-anthropic", mint_only=True)
    assert mint["mint_only"] is True

    with pytest.raises(RuntimeError, match="fetch layer failed"):
        mcp.tools["cortex_brief"](seat="web-anthropic", mint_only=False)


@pytest.mark.parametrize("seat", ["web-anthropic", "cursor"])
def test_mint_only_wire_under_2kb(seat: str, monkeypatch: Any) -> None:
    def _cx(_method: str, _path: str, body: dict[str, Any] | None = None, **_kw: Any) -> dict:
        assert body is not None
        return perform_boot_mint(body["seat"], at=_FIXED)

    monkeypatch.setattr(orchestration_tools, "cx", _cx)
    monkeypatch.setattr(orchestration_tools, "record", lambda *_a, **_k: None)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    payload = mcp.tools["cortex_brief"](seat=seat, mint_only=True)

    guarded = asyncio.run(_guard_cortex_brief(_boot_tool_result(payload)))
    wire_bytes = _measure_result(guarded)
    assert wire_bytes < 2000


def test_generated_manifest_includes_boot_mint() -> None:
    from cortex_store.openapi_mcp import generated_adapter_manifest as manifest

    assert "POST /boot-mint" in manifest.NON_BINDING_PATH_FINGERPRINTS
