"""Unit tests for cortex-pinned deliverable sandbox resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.git_integration_worker.cursor_sdk_deliverables import (
    PinnedResolution,
    resolve_cortex_pinned_deliverables,
)


@pytest.mark.asyncio
async def test_pinning_reads_from_cortex_mount(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    source_repo = tmp_path / "repo"
    rel = "notes/system/foo.md"
    cortex_path = cortex_root / rel
    cortex_path.parent.mkdir(parents=True)
    cortex_path.write_text("cortex content", encoding="utf-8")
    source_repo.mkdir()

    writer = AsyncMock(
        return_value={"uri": "cortex://notes/system/foo.md", "created": False}
    )
    result = await resolve_cortex_pinned_deliverables(
        files_expected=[f"cortex://{rel}"],
        full_text="fallback",
        source_repo=source_repo,
        dispatch_id="d1",
        thread_id="t1",
        post_pinned=writer,
        cortex_root=cortex_root,
    )

    assert isinstance(result, PinnedResolution)
    assert result.uris == ["cortex://notes/system/foo.md"]
    assert result.satisfied_rels == (rel,)
    assert result.divergent_rels == ()
    writer.assert_awaited_once()
    call = writer.await_args.kwargs
    assert call["content"] == "cortex content"
    assert call["write_if_absent"] is False


@pytest.mark.asyncio
async def test_pinning_wrong_sandbox_marks_divergent(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    source_repo = tmp_path / "repo"
    rel = "notes/system/bar.md"
    repo_path = source_repo / rel
    repo_path.parent.mkdir(parents=True)
    repo_path.write_text("workspaces content", encoding="utf-8")
    cortex_root.mkdir()

    writer = AsyncMock(
        return_value={"uri": "cortex://notes/system/bar.md", "created": True}
    )
    result = await resolve_cortex_pinned_deliverables(
        files_expected=[f"cortex:{rel}"],
        full_text="fallback",
        source_repo=source_repo,
        dispatch_id="d2",
        thread_id="t2",
        post_pinned=writer,
        cortex_root=cortex_root,
    )

    assert f"pinned_deliverable_wrong_sandbox:{rel}" in result.divergent_rels
    assert rel not in result.satisfied_rels
    call = writer.await_args.kwargs
    assert call["content"] == "workspaces content"
    assert call["write_if_absent"] is False


@pytest.mark.asyncio
async def test_pinning_absent_both_uses_write_if_absent(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    source_repo = tmp_path / "repo"
    rel = "notes/system/missing.md"
    cortex_root.mkdir()
    source_repo.mkdir()

    writer = AsyncMock(
        return_value={"uri": "cortex://notes/system/missing.md", "created": True}
    )
    result = await resolve_cortex_pinned_deliverables(
        files_expected=[f"cortex://{rel}"],
        full_text="closeout body",
        source_repo=source_repo,
        dispatch_id="d3",
        thread_id="t3",
        post_pinned=writer,
        cortex_root=cortex_root,
    )

    assert result.satisfied_rels == (rel,)
    call = writer.await_args.kwargs
    assert call["content"] == "closeout body"
    assert call["write_if_absent"] is True
