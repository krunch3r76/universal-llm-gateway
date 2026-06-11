"""fs accepts optional description kwarg for create_file-family call-compat (a16834)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")
os.environ.setdefault("PROJECT_ROOT", str(REPO_ROOT.parent))


@pytest.fixture(scope="module")
def fs_client() -> Client:
    from server import _build_server  # noqa: PLC0415

    mcp, _overflow_md, _overflow_reg = _build_server()
    return Client(mcp)


@pytest.mark.asyncio
async def test_description_kwarg_not_rejected(fs_client: Client) -> None:
    """description is accepted; validation proceeds to required-param checks."""
    async with fs_client:
        res = await fs_client.call_tool(
            "fs",
            {
                "op": "read",
                "description": "annotate bus sidecar write",
                "path": "universal-llm-gateway/AGENTS.md",
            },
        )
    env = res.structured_content
    assert env.get("error_type") == "ValidationError"
    errors = env.get("errors") or []
    assert not any(e.get("param") == "description" for e in errors)
    assert any(e.get("param") == "sandbox" for e in errors)


@pytest.mark.asyncio
async def test_description_kwarg_ignored_on_successful_read(fs_client: Client) -> None:
    async with fs_client:
        res = await fs_client.call_tool(
            "fs",
            {
                "op": "read",
                "sandbox": "workspaces",
                "description": "noop annotation",
                "path": "universal-llm-gateway/AGENTS.md",
                "limit": 1,
            },
        )
    assert getattr(res, "is_error", False) is False
    body = res.structured_content
    assert "description" not in body
    assert body.get("path") == "universal-llm-gateway/AGENTS.md"
