"""Build identity on ToolResult.meta — stamp projection and in-process tools/call."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _deploy_stamp import read_source_sync_stamp
from build_identity import build_ulg_meta, register_build_identity


def test_build_ulg_meta_carries_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text(
        "2026-08-15T09:30:00Z\n"
        "a28906fc180db15916f61f00eb61a633b7781689\n"
        "image_id=sha256:abc\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("_deploy_stamp._STAMP_PATH", stamp)
    assert build_ulg_meta()["image_id"] == "sha256:abc"


def test_build_ulg_meta_omits_empty_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("_deploy_stamp._STAMP_PATH", tmp_path / "missing")
    meta = build_ulg_meta()
    assert "image_id" not in meta
    assert read_source_sync_stamp()["image_id"] is None


@pytest.mark.asyncio
async def test_tools_call_meta_ulg_includes_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text(
        "2026-08-15T09:30:00Z\n"
        "a28906fc180db15916f61f00eb61a633b7781689\n"
        "image_id=sha256:abc\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("_deploy_stamp._STAMP_PATH", stamp)

    mcp = FastMCP("test-build-identity")

    @mcp.tool()
    def ping() -> str:
        return "ok"

    register_build_identity(mcp)
    client = Client(mcp)
    async with client:
        result = await client.call_tool("ping", {})
    ulg = (result.meta or {}).get("ulg") or {}
    assert ulg.get("image_id") == "sha256:abc"
