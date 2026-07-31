"""Delegate MCP relay — import graph, schema pin, passthrough, firewall."""

from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"
DELEGATE_MODULE = MCP_SERVER_DIR / "tools" / "delegate.py"

sys.path.insert(0, str(MCP_SERVER_DIR))
sys.path.insert(0, str(REPO_ROOT / "libs"))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

_DISPATCH_IMPORT_MARKERS = (
    "cursor_sdk_generate",
    "dispatch_cursor_sdk_generate",
    "generate_wrap",
    "implement_admission_bridge",
    "team_router",
    "panel_dispatch",
    "team_dispatch",
)


def _life_server() -> dict[str, Any]:
    from server import _build_server

    mcp, overflow_md, overflow_reg = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tools": tools,
        "tool_names": {t.name for t in tools},
        "overflow_md": overflow_md,
        "overflow_reg": overflow_reg,
    }


def _delegate_tool_fn(server: dict[str, Any]):
    tools = asyncio.run(server["mcp"].list_tools())
    delegate_tool = next(t for t in tools if t.name == "delegate")
    return delegate_tool.fn, delegate_tool


def test_delegate_import_graph_has_no_dispatch_impls() -> None:
    source = DELEGATE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for marker in _DISPATCH_IMPORT_MARKERS:
                assert marker not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                for marker in _DISPATCH_IMPORT_MARKERS:
                    assert marker not in alias.name


def test_served_schema_verb_enum_matches_registry() -> None:
    from life_intent.registry import load_registry

    server = _life_server()
    _, delegate_tool = _delegate_tool_fn(server)
    mcp_tool = delegate_tool.to_mcp_tool()
    schema = mcp_tool.inputSchema or {}
    props = schema.get("properties") or {}
    intent = props.get("intent") or {}
    intent_props = intent.get("properties") or {}
    verb_prop = intent_props.get("verb") or {}
    served_enum = verb_prop.get("enum")
    assert served_enum == load_registry().render_verb_enum()


def test_malformed_registry_fails_closed_at_load(tmp_path: Path) -> None:
    from life_intent.registry import load_registry

    path = tmp_path / "bad.yaml"
    path.write_text("verbs: {}\nrefuse_list: [dispatch]\n")
    with pytest.raises(ValueError, match="version"):
        load_registry(path)


def test_descriptor_firewall_has_no_dispatch_vocabulary() -> None:
    from life_intent.response_firewall import forbidden_hits

    raw = yaml.safe_load(CANONICAL_YAML.read_text(encoding="utf-8"))
    texts: list[str] = []
    for row in raw.get("tools", []):
        if row.get("domain") != "delegate":
            continue
        desc = row.get("fol_descriptor")
        if isinstance(desc, str):
            texts.append(desc)
    source = DELEGATE_MODULE.read_text(encoding="utf-8")
    texts.append(source.split('"""', 3)[1])
    for text in texts:
        assert forbidden_hits(text) == []


def test_relay_propose_passes_through_verbatim() -> None:
    from tools.delegate import register_delegate_tools

    class _CaptureMCP:
        def tool(self, *args: object, **kwargs: object):  # noqa: ANN201
            def decorator(fn: object) -> object:
                self.fn = fn
                return fn

            if args and callable(args[0]) and not kwargs:
                self.fn = args[0]
                return args[0]
            return decorator

    mcp = _CaptureMCP()
    register_delegate_tools(mcp)
    payload = {
        "proposal_id": "abc",
        "work_order": "scout",
        "rejects": [],
        "context": "cortex.life-intent/v1",
    }

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return payload

    class _FakeClient:
        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, json: dict[str, Any]) -> _FakeResponse:  # noqa: A002
            assert path == "/api/v1/life/intent/propose"
            assert json == {
                "intent": {
                    "verb": "investigate",
                    "subject": "latency",
                    "detail": "Dashboard loads slowly on weekday mornings.",
                }
            }
            return _FakeResponse()

    with patch(
        "tools.delegate.make_sync_client",
        return_value=_FakeClient(),
    ):
        result = mcp.fn(
            "propose",
            arguments='{"intent":{"verb":"investigate","subject":"latency",'
            '"detail":"Dashboard loads slowly on weekday mornings."}}',
        )
    assert result == payload


def test_relay_commit_gated_passthrough_verbatim() -> None:
    from tools.delegate import register_delegate_tools

    class _CaptureMCP:
        def tool(self, *args: object, **kwargs: object):  # noqa: ANN201
            def decorator(fn: object) -> object:
                self.fn = fn
                return fn

            if args and callable(args[0]) and not kwargs:
                self.fn = args[0]
                return args[0]
            return decorator

    mcp = _CaptureMCP()
    register_delegate_tools(mcp)
    payload = {
        "committed": False,
        "rejects": [{"code": "commit_gated", "detail": "Commit is gated off."}],
        "context": "cortex.life-intent/v1",
    }

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return payload

    class _FakeClient:
        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, json: dict[str, Any]) -> _FakeResponse:  # noqa: A002
            assert path == "/api/v1/life/intent/commit"
            assert json == {"proposal_id": "deadbeef"}
            return _FakeResponse()

    with patch(
        "tools.delegate.make_sync_client",
        return_value=_FakeClient(),
    ):
        result = mcp.fn("commit", arguments='{"proposal_id":"deadbeef"}')
    assert result == payload


def test_relay_transport_error_shape() -> None:
    from tools.delegate import register_delegate_tools

    class _CaptureMCP:
        def tool(self, *args: object, **kwargs: object):  # noqa: ANN201
            def decorator(fn: object) -> object:
                self.fn = fn
                return fn

            if args and callable(args[0]) and not kwargs:
                self.fn = args[0]
                return args[0]
            return decorator

    mcp = _CaptureMCP()
    register_delegate_tools(mcp)

    class _FakeClient:
        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, path: str, json: dict[str, Any]) -> httpx.Response:  # noqa: A002
            raise httpx.ConnectError("refused", request=MagicMock())

    with patch(
        "tools.delegate.make_sync_client",
        return_value=_FakeClient(),
    ):
        result = mcp.fn("propose", arguments='{"intent":{"verb":"fix","subject":"x","detail":"long enough detail"}}')
    assert "error" in result
    assert result.get("status_code") is None
