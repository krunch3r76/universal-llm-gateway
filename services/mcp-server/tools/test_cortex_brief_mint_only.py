"""cortex_brief mint_only MCP wrapper tests."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent
from response_size_guard import _measure_result

from tools.cortex_named_tools import _orchestration_tools as orchestration_tools
from tools.cortex_named_tools._boot_runner import BootMode

_FIXED = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)

_MINT_PAYLOAD = {
    "session_id": "web-anthropic-2026-11-23-080000-abc",
    "session_identity": {
        "session_id": "web-anthropic-2026-11-23-080000-abc",
        "agent": "claude-web",
    },
    "boot_identity": {
        "family": "claude",
        "platform": "web",
        "agent": "claude-web",
        "capability_slug": "claude-web",
        "session_prefix": "web-anthropic",
        "inbox_recipient": "claude-web",
        "self_entity_id": "ai_agent:claude",
        "is_logical": False,
    },
    "mode": "live",
    "utc_now": "2026-11-23T08:00:00Z",
    "local_time": "2026-11-23T00:00:00-0800",
    "mint_only": True,
    "briefing_card_hint": "For the full briefing card, call cortex_brief without mint_only=true.",
}

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


def test_mint_only_returns_without_briefing_card(monkeypatch: Any) -> None:
    events: list[str] = []

    def _cx(method: str, path: str, body: dict[str, Any] | None = None, **_kw: Any) -> dict:
        assert method == "POST"
        assert path == "/boot-mint"
        assert body and body.get("seat") == "web-anthropic"
        return dict(_MINT_PAYLOAD)

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
    def _cx(*_a: Any, **_k: Any) -> dict[str, Any]:
        return dict(_MINT_PAYLOAD)

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
    payload = dict(_MINT_PAYLOAD)
    if seat == "cursor":
        payload = {
            **payload,
            "session_id": "cursor-2026-11-23-080000-abc",
            "session_identity": {
                "session_id": "cursor-2026-11-23-080000-abc",
                "agent": "claude-cursor",
            },
            "boot_identity": {
                **payload["boot_identity"],
                "platform": "cursor",
                "agent": "claude-cursor",
                "capability_slug": "claude-cursor",
                "session_prefix": "cursor",
                "inbox_recipient": "claude-cursor",
            },
        }

    monkeypatch.setattr(orchestration_tools, "cx", lambda *_a, **_k: dict(payload))
    monkeypatch.setattr(orchestration_tools, "record", lambda *_a, **_k: None)

    mcp = _DummyMcp()
    orchestration_tools.register_orchestration_tools(mcp)
    result = mcp.tools["cortex_brief"](seat=seat, mint_only=True)

    wire_bytes = _measure_result(_boot_tool_result(result))
    assert wire_bytes < 2000


def test_generated_manifest_includes_boot_mint() -> None:
    from cortex_store.openapi_mcp import generated_adapter_manifest as manifest

    assert "POST /boot-mint" in manifest.NON_BINDING_PATH_FINGERPRINTS
