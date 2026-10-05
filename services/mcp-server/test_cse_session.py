"""MCP cse_session relay purity and catalog parity gates."""

from __future__ import annotations

import ast
import asyncio
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


def test_cse_session_on_both_surfaces(life_server: dict, code_server: dict) -> None:
    assert "cse_session" in life_server["tool_names"]
    assert "cse_session" in code_server["tool_names"]
    assert "cse_session" in life_server["primary"]
    assert "cse_session" in code_server["primary"]


def test_project_ask_absent_both_surfaces(life_server: dict, code_server: dict) -> None:
    from claude_bundles.operator_proxy_mission import LIFE_SURFACE_FORBIDDEN_TOOLS
    from endpoint_surface import derive_code_extra_primary_tools

    assert "project_ask" not in life_server["primary"]
    assert "project_ask" not in code_server["primary"]
    assert "project_ask" not in life_server["tool_names"]
    assert "project_ask" not in code_server["tool_names"]
    derived = derive_code_extra_primary_tools()
    assert LIFE_SURFACE_FORBIDDEN_TOOLS == frozenset({"panel_dispatch", "claudeburst"})
    assert LIFE_SURFACE_FORBIDDEN_TOOLS < derived
    assert "project_ask" not in LIFE_SURFACE_FORBIDDEN_TOOLS
    assert "cse_session" not in LIFE_SURFACE_FORBIDDEN_TOOLS


def test_per_op_mandate_safety_in_catalog() -> None:
    import yaml

    raw = yaml.safe_load(CANONICAL_YAML.read_text(encoding="utf-8"))
    by_name = {row["canonical_name"]: row for row in raw["tools"]}
    assert by_name["cse_session_provenance"]["mandate_safety"] == "read_only"
    assert by_name["cse_session_harvest"]["mandate_safety"] == "read_only"
    assert by_name["cse_session_paste"]["mandate_safety"] == "write"
    assert by_name["cse_session_followup"]["mandate_safety"] == "write"
    assert by_name["cse_session_resolve_attended"]["mandate_safety"] == "read_only"


def _fake_client(body: dict, status: int = 200):
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status.return_value = None
    client = MagicMock()
    client.get.return_value = resp
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


def test_relay_attended_forwards_parent_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    import tools.cse_session_warm as warm

    client = _fake_client(
        {
            "state": "current",
            "basis": "in_flight",
            "current": {"chat_url": "https://claude.ai/cowork/cse_live"},
        }
    )
    monkeypatch.setenv("PROJECT_ASK_URL", "http://cdp-ask")
    monkeypatch.setattr(warm.httpx, "Client", MagicMock(return_value=client))
    result = warm.relay_attended(parent_thread="12286")
    params = client.get.call_args.kwargs["params"]
    assert params == {"parent_thread": "12286"}
    assert result["current"]["chat_url"] == "https://claude.ai/cowork/cse_live"
    assert client.get.call_count == 1


def test_relay_attended_new_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    import tools.cse_session_warm as warm

    client = _fake_client(
        {
            "code": "lane_cse_ambiguous",
            "state": "ambiguous",
            "current": None,
            "candidates": [],
        },
        status=409,
    )
    monkeypatch.setenv("PROJECT_ASK_URL", "http://cdp-ask")
    monkeypatch.setattr(warm.httpx, "Client", MagicMock(return_value=client))
    result = warm.relay_attended(parent_thread="12286")
    assert result["code"] == "lane_cse_ambiguous"
    assert result["retryable"] is True
    assert "data.candidates" in result["message"]
    assert result["data"]["state"] == "ambiguous"

    client_none = _fake_client({"code": "lane_cse_none", "state": "none"}, status=404)
    monkeypatch.setattr(warm.httpx, "Client", MagicMock(return_value=client_none))
    none = warm.relay_attended(parent_thread="12286")
    assert none["code"] == "lane_cse_none"
    assert none["retryable"] is True

    client_err = _fake_client(
        {"code": "lane_cse_probe_error", "state": "none", "reason": "probe_error"},
        status=503,
    )
    monkeypatch.setattr(warm.httpx, "Client", MagicMock(return_value=client_err))
    err = warm.relay_attended(parent_thread="12286")
    assert err["code"] == "lane_cse_probe_error"
    assert err["retryable"] is True


def test_relay_module_has_no_bundle_imports() -> None:
    for rel in ("cse_session.py", "cse_session_warm.py"):
        source = (MCP_SERVER_DIR / "tools" / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.names[0].name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        }
        import_from = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "claude_bundles" not in imports
        assert "cdp_ask" not in imports
        assert not any(m and m.startswith("claude_bundles") for m in import_from)
        assert not any(m and m.startswith("cdp_ask") for m in import_from)
