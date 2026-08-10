"""Supervision for GIW lifespan background loops.

When a background loop raises, the exception is stored on its ``asyncio.Task``.
Because the lifespan pins every task on ``app.state``, the task is never garbage
collected, so asyncio's "Task exception was never retrieved" warning never
fires: the loop is simply gone, with no log line, no event, and no alarm.

That is the 2026-08-09 cursor-auto outage. ``auto_worker_loop`` unwound at
21:27:58Z when ``await hb_task`` re-raised a dead heartbeat's exception; the
handler deregistered in its ``finally`` and ``lane:cursor-auto`` read
``handler_count: 0`` for the next four hours. Everything commissioned through
``agent_bus.request`` parked behind it — including ``contract: propagate``, the
sanctioned repair path, which is why no agent could fix it.

Two duties, in order of importance: make the death legible, then respawn the
loops for which a gap is worse than a restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

LoopFactory = Callable[[], Coroutine[Any, Any, None]]

_DEFAULT_MAX_RESTARTS = 10
_RESTART_DELAY_S = 1.0


def supervise(
    app: Any,
    attr: str,
    factory: LoopFactory,
    *,
    restart: bool = False,
    max_restarts: int = _DEFAULT_MAX_RESTARTS,
) -> asyncio.Task[None]:
    """Start a lifespan loop, stash it on ``app.state``, and watch it die.

    ``restart`` is opt-in per loop: respawn is right for a loop whose absence
    silently parks the fleet, and wrong for one whose repeated failure should
    stay visible. ``max_restarts`` bounds a crash loop so a permanently broken
    loop degrades to the old behaviour instead of spinning.
    """
    task = asyncio.create_task(factory(), name=attr)
    setattr(app.state, attr, task)
    task.add_done_callback(
        lambda finished: _on_loop_exit(
            app,
            attr,
            factory,
            finished,
            restart=restart,
            restarts_left=max_restarts,
        )
    )
    return task


def _on_loop_exit(
    app: Any,
    attr: str,
    factory: LoopFactory,
    task: asyncio.Task[None],
    *,
    restart: bool,
    restarts_left: int,
) -> None:
    """Log why a background loop ended and respawn it when supervised."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        logger.warning("giw background loop exited without error: attr=%s", attr)
    else:
        logger.error(
            "giw background loop died: attr=%s exc=%s: %s",
            attr,
            type(exc).__name__,
            exc,
            exc_info=exc,
        )
    if not restart or getattr(app.state, "shutting_down", False):
        return
    if restarts_left <= 0:
        logger.error(
            "giw background loop not respawned (restart budget exhausted): attr=%s",
            attr,
        )
        return
    asyncio.create_task(
        _respawn(app, attr, factory, restarts_left=restarts_left),
        name=f"{attr}-respawn",
    )


async def _respawn(
    app: Any,
    attr: str,
    factory: LoopFactory,
    *,
    restarts_left: int,
) -> None:
    """Re-arm a supervised loop after a short delay."""
    await asyncio.sleep(_RESTART_DELAY_S)
    if getattr(app.state, "shutting_down", False):
        return
    logger.warning(
        "giw background loop respawning: attr=%s restarts_left=%d",
        attr,
        restarts_left - 1,
    )
    supervise(
        app,
        attr,
        factory,
        restart=True,
        max_restarts=restarts_left - 1,
    )


__all__ = ["supervise"]
