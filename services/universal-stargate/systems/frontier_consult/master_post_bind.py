"""Schedule master-only aux loops so they cannot hold the :9999 bind.

Uvicorn binds the listen socket only after the lifespan startup half returns
(the ``yield``). Importing or awaiting the review-child listener and the CDP
reconcile before that yield runs on the same thread that must reach the yield.
Those imports and their first socket calls therefore happen in a task whose
first await is scheduled, and the imports themselves run on a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from universal_logging import get_logger

logger = get_logger(__name__)

Starter = Callable[[], Awaitable[None]]


def _load_starters() -> tuple[Starter, Starter]:
    """Import the aux starters off the event-loop thread."""
    from .cdp_generate_reconcile import start_cdp_generate_reconcile
    from .review_child_spawn_hook import start_review_child_spawn_listener

    return start_review_child_spawn_listener, start_cdp_generate_reconcile


async def _run_master_post_bind() -> None:
    """Yield once so lifespan can return, then start the aux loops."""
    await asyncio.sleep(0)
    logger.info("master post-bind aux starting")
    try:
        start_listener, start_reconcile = await asyncio.to_thread(_load_starters)
        await start_listener()
        await start_reconcile()
    except Exception:
        logger.exception("master post-bind aux failed")
        return
    logger.info("master post-bind aux scheduled")


def schedule_master_post_bind() -> asyncio.Task[None]:
    """Schedule aux startup without running it before the caller yields."""
    return asyncio.create_task(_run_master_post_bind(), name="master-post-bind")
