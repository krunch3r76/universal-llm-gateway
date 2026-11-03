"""In-process target_lock map: no leak after contended loops, cancel, exclusion."""

from __future__ import annotations

import asyncio
import gc
import os
import threading
import weakref
from typing import Any

import pytest

from web_tab.target_lock import _INPROC, cdp_open_bind_lock, target_write_lock

pytestmark = pytest.mark.offline


@pytest.fixture
def web_tab_lock_dir(tmp_path: pytest.TempPathFactory) -> None:
    os.environ["ULG_WEB_TAB_LOCK_DIR"] = str(tmp_path / "locks")


async def _contend_two(
    lock_factory: Any,
    *,
    hold_s: float = 0.05,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with lock_factory():
            entered.set()
            await release.wait()

    async def waiter() -> None:
        await entered.wait()
        async with lock_factory():
            pass

    h = asyncio.create_task(holder())
    w = asyncio.create_task(waiter())
    await asyncio.sleep(hold_s)
    release.set()
    await asyncio.gather(h, w)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: target_write_lock("leak-target", timeout_s=5.0),
        lambda: cdp_open_bind_lock("http://127.0.0.1:9222/leak"),
    ],
)
def test_contended_runs_drop_inproc_map(
    web_tab_lock_dir: None, factory: Any
) -> None:
    refs: list[weakref.ref[Any]] = []

    for _ in range(50):

        async def main() -> None:
            loop = asyncio.get_running_loop()
            refs.append(weakref.ref(loop))
            await _contend_two(factory)

        asyncio.run(main())

    gc.collect()
    assert all(r() is None for r in refs)
    assert len(_INPROC) == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_clears_inproc_map(web_tab_lock_dir: None) -> None:
    entered = asyncio.Event()

    async def holder() -> None:
        async with target_write_lock("cancel-target", timeout_s=5.0):
            entered.set()
            await asyncio.sleep(2.0)

    async def waiter() -> None:
        await entered.wait()
        async with target_write_lock("cancel-target", timeout_s=5.0):
            pass

    h = asyncio.create_task(holder())
    await entered.wait()
    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)
    w.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w
    h.cancel()
    with pytest.raises(asyncio.CancelledError):
        await h
    assert len(_INPROC) == 0


@pytest.mark.asyncio
async def test_inproc_mutual_exclusion_one_loop(web_tab_lock_dir: None) -> None:
    depth = 0
    max_depth = 0
    guard = threading.Lock()

    async def one() -> None:
        nonlocal depth, max_depth
        async with target_write_lock("mutex-target", timeout_s=5.0):
            with guard:
                depth += 1
                max_depth = max(max_depth, depth)
            await asyncio.sleep(0.02)
            with guard:
                depth -= 1

    await asyncio.gather(one(), one())
    assert max_depth == 1
