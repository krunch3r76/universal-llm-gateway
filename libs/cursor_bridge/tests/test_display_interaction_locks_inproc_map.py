"""In-process display lock maps: no leak after contended loops, cancel, exclusion."""

from __future__ import annotations

import asyncio
import gc
import os
import threading
import weakref
from typing import Any

import pytest
from cursor_bridge import display_interaction_locks as dil_mod
from cursor_bridge.display_interaction_locks import (
    _INPROC_AGENT,
    _INPROC_SWITCH,
    agent_paste_lock,
    display_switch_lock,
)

pytestmark = pytest.mark.offline


@pytest.fixture
def display_lock_dir(tmp_path: pytest.TempPathFactory) -> None:
    os.environ["ULG_DISPLAY_LOCK_DIR"] = str(tmp_path / "locks")


async def _contend_two(lock_factory: Any, *, hold_s: float = 0.05) -> None:
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
    "factory,map_ref",
    [
        (lambda: agent_paste_lock("d1", "a1"), "_INPROC_AGENT"),
        (lambda: display_switch_lock("d1"), "_INPROC_SWITCH"),
    ],
)
def test_contended_runs_drop_inproc_map(
    display_lock_dir: None, factory: Any, map_ref: str
) -> None:
    inproc_map = _INPROC_AGENT if map_ref == "_INPROC_AGENT" else _INPROC_SWITCH
    refs: list[weakref.ref[Any]] = []

    for _ in range(50):

        async def main() -> None:
            loop = asyncio.get_running_loop()
            refs.append(weakref.ref(loop))
            await _contend_two(factory)

        asyncio.run(main())

    gc.collect()
    assert all(r() is None for r in refs)
    assert len(inproc_map) == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_clears_agent_inproc_map(
    display_lock_dir: None,
) -> None:
    entered = asyncio.Event()

    async def holder() -> None:
        async with agent_paste_lock("d2", "a2"):
            entered.set()
            await asyncio.sleep(2.0)

    async def waiter() -> None:
        await entered.wait()
        async with agent_paste_lock("d2", "a2"):
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
    assert len(_INPROC_AGENT) == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_clears_switch_inproc_map(
    display_lock_dir: None,
) -> None:
    entered = asyncio.Event()

    async def holder() -> None:
        async with display_switch_lock("d-switch"):
            entered.set()
            await asyncio.sleep(2.0)

    async def waiter() -> None:
        await entered.wait()
        async with display_switch_lock("d-switch"):
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
    assert len(_INPROC_SWITCH) == 0


@pytest.mark.asyncio
async def test_agent_paste_lock_inproc_mutex_with_flock_noop(
    display_lock_dir: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process layer alone serializes two tasks on one loop (flock bypassed)."""

    async def noop_acquire(self: object) -> None:
        return None

    monkeypatch.setattr(dil_mod._FcntlFileLock, "acquire", noop_acquire)
    monkeypatch.setattr(dil_mod._FcntlFileLock, "release", lambda self: None)

    depth = 0
    max_depth = 0
    guard = threading.Lock()

    async def one() -> None:
        nonlocal depth, max_depth
        async with agent_paste_lock("d3", "a3"):
            with guard:
                depth += 1
                max_depth = max(max_depth, depth)
            await asyncio.sleep(0.02)
            with guard:
                depth -= 1

    await asyncio.gather(one(), one())
    assert max_depth == 1
