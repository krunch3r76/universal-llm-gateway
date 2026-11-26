"""MCP chat_session relay purity and catalog parity gates."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

_FORBIDDEN_CHAT_SESSION_MODULES = frozenset(
    {"claude_bundles", "web_chat_relay", "chat_harvest"}
)


@pytest.fixture(scope="module")
def life_server() -> dict:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    return {
        "tool_names": {t.name for t in tools},
        "primary": derive_surface_primary_tools("life"),
    }


@pytest.fixture(scope="module")
def code_server() -> dict:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    return {
        "tool_names": {t.name for t in tools},
        "primary": derive_surface_primary_tools("code"),
    }


def test_chat_session_on_code_only(life_server: dict, code_server: dict) -> None:
    assert "chat_session" not in life_server["tool_names"]
    assert "chat_session" in code_server["tool_names"]
    assert "chat_session" not in life_server["primary"]
    assert "chat_session" in code_server["primary"]


def test_per_op_mandate_safety_in_catalog() -> None:
    import yaml

    raw = yaml.safe_load(CANONICAL_YAML.read_text(encoding="utf-8"))
    by_name = {row["canonical_name"]: row for row in raw["tools"]}
    assert by_name["chat_session_harvest"]["mandate_safety"] == "read_only"
    assert by_name["chat_session_probe"]["mandate_safety"] == "read_only"
    assert by_name["chat_session_observe"]["mandate_safety"] == "read_only"
    observe_schema = by_name["chat_session_observe"]["json_schema"]["properties"]
    for key in ("match_terms", "max_nodes", "open_on_demand"):
        assert key in observe_schema
    assert by_name["chat_session_paste"]["mandate_safety"] == "write"


def test_chat_session_module_has_no_forbidden_imports() -> None:
    source = (MCP_SERVER_DIR / "tools" / "chat_session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)
    }
    import_from = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imports.intersection(_FORBIDDEN_CHAT_SESSION_MODULES)
    assert not any(
        m and any(m.startswith(prefix) for prefix in _FORBIDDEN_CHAT_SESSION_MODULES)
        for m in import_from
    )


PROBE_BRANCH_SHA = (
    "0fa031161473c7f2e85358471af826e85bc9dba4a88cba2383912d2af625a826"
)


def test_probe_branch_frozen() -> None:
    source = (MCP_SERVER_DIR / "tools" / "chat_session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            seg = ast.get_source_segment(source, node)
            if seg and 'op == "probe"' in seg.replace("'", '"'):
                assert hashlib.sha256(seg.encode()).hexdigest() == PROBE_BRANCH_SHA
                return
    raise AssertionError("probe branch not found")


def test_probe_relay_body_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    def _relay(method: str, path: str, *, json_body: dict[str, object] | None = None, **_k: object):
        recorded.append((path, dict(json_body or {})))
        return {"outcome": "harvested"}

    monkeypatch.setattr("tools.chat_session._relay", _relay)
    from server import _build_server

    mcp, _, _ = _build_server("code")
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "chat_session")
    tool.fn(
        op="probe",
        url="https://claude.ai/chat/x",
        metadata_only=False,
    )
    assert recorded[0][0] == "/v1/chat-session/probe"
    body = recorded[0][1]
    for key in ("match_terms", "max_nodes", "open_on_demand"):
        assert key not in body


def test_observe_relay_body(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[str, dict[str, object]]] = []

    def _relay(method: str, path: str, *, json_body: dict[str, object] | None = None, **_k: object):
        recorded.append((path, dict(json_body or {})))
        return {"outcome": "observed"}

    monkeypatch.setattr("tools.chat_session._relay", _relay)
    from server import _build_server

    mcp, _, _ = _build_server("code")
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "chat_session")
    tool.fn(
        op="observe",
        url="https://claude.ai/chat/x",
        match_terms=["paused"],
        open_on_demand=False,
    )
    assert recorded[0][0] == "/v1/chat-session/observe"
    assert set(recorded[0][1].keys()).issubset(
        {"url", "site", "cdp_url", "match_terms", "max_nodes", "open_on_demand"}
    )


def test_observe_forwards_open_on_demand_false(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, object]] = []

    def _relay(method: str, path: str, *, json_body: dict[str, object] | None = None, **_k: object):
        recorded.append(dict(json_body or {}))
        return {"outcome": "observed"}

    monkeypatch.setattr("tools.chat_session._relay", _relay)
    from server import _build_server

    mcp, _, _ = _build_server("code")
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "chat_session")
    tool.fn(
        op="observe",
        url="https://claude.ai/chat/x",
        match_terms=["a", "b"],
        open_on_demand=False,
    )
    assert recorded[0]["open_on_demand"] is False


def test_observe_409_through_real_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeResp:
        status_code = 409

        @property
        def content(self) -> bytes:
            return b"x"

        def json(self) -> dict[str, object]:
            return {
                "code": "invalid_pattern",
                "message": "bad",
                "source": "gateway",
                "retryable": False,
                "data": {"field": "match_terms", "limit": "1..8", "got": "0"},
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def request(self, *args: object, **kwargs: object) -> FakeResp:
            return FakeResp()

    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:5999")
    monkeypatch.setattr(httpx, "Client", FakeClient)
    from tools.relay import _relay

    out = _relay(
        "POST",
        "/v1/chat-session/observe",
        json_body={"url": "https://claude.ai/chat/x", "match_terms": []},
    )
    assert out["code"] == "invalid_pattern"
    assert out.get("ok") is False
    assert out["data"]["data"]["field"] == "match_terms"
