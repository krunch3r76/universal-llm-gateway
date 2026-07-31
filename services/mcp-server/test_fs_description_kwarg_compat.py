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


def _sandbox_error(res) -> dict:
    env = res.structured_content
    assert env.get("error_type") == "ValidationError"
    errors = env.get("errors") or []
    sandbox_errors = [e for e in errors if e.get("param") == "sandbox"]
    assert len(sandbox_errors) == 1
    return sandbox_errors[0]


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
            raise_on_error=False,
        )
    assert getattr(res, "is_error", False) is True
    err = _sandbox_error(res)
    assert not any(
        e.get("param") == "description"
        for e in (res.structured_content.get("errors") or [])
    )
    hint = err.get("hint") or ""
    assert hint
    assert "workspaces" in hint
    assert "looks like workspaces" in hint


@pytest.mark.asyncio
async def test_missing_sandbox_ambiguous_path_hint(fs_client: Client) -> None:
    async with fs_client:
        res = await fs_client.call_tool(
            "fs",
            {"op": "read", "path": "notes/system/threads/x.md"},
            raise_on_error=False,
        )
    assert getattr(res, "is_error", False) is True
    err = _sandbox_error(res)
    hint = err.get("hint") or ""
    assert "BOTH stores" in hint
    assert "disambiguate explicitly" in hint


@pytest.mark.asyncio
async def test_missing_sandbox_generic_path_hint(fs_client: Client) -> None:
    async with fs_client:
        res = await fs_client.call_tool(
            "fs",
            {"op": "read", "path": "dropbox/inbox/foo.pdf"},
            raise_on_error=False,
        )
    assert getattr(res, "is_error", False) is True
    err = _sandbox_error(res)
    hint = err.get("hint") or ""
    assert "Pass sandbox=cortex or sandbox=workspaces" in hint


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
    assert body.get("path", "").endswith("AGENTS.md")
