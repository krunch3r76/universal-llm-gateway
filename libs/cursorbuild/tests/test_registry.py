"""Registry two-writer safety tests."""

from __future__ import annotations

import pytest

from cursorbuild.registry import get_dispatch_id, release_cwd, try_acquire_cwd


@pytest.mark.asyncio
async def test_edit_blocks_second_edit() -> None:
    assert await try_acquire_cwd("/tmp/a", "d1", mode="edit")
    assert not await try_acquire_cwd("/tmp/a", "d2", mode="edit")
    assert await get_dispatch_id("/tmp/a") == "d1"
    await release_cwd("/tmp/a", "d1")
    assert await try_acquire_cwd("/tmp/a", "d2", mode="edit")


@pytest.mark.asyncio
async def test_read_only_readers_coexist() -> None:
    assert await try_acquire_cwd("/tmp/b", "r1", mode="read_only")
    assert await try_acquire_cwd("/tmp/b", "r2", mode="read_only")
    await release_cwd("/tmp/b", "r1")
    await release_cwd("/tmp/b", "r2")
