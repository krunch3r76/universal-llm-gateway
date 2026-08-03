"""Post-bind startup persistence for git-integration-worker.

Boot-time ledger reconcile / closeout replay / auto-job reconcile must never
block uvicorn from binding the health port. Unbounded work or network waits
in the pre-yield lifespan path take the fleet executor down (2026-08-03 hang:
~43 GB RSS, :8091 never listening).
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.closeout_replay import (
    startup_closeout_outbox_replay,
)
from services.git_integration_worker.cursor_auto.job_reconcile import (
    startup_auto_job_reconcile,
)
from services.git_integration_worker.routes.cursor_sdk import (
    startup_ledger_reconcile,
)

logger = get_logger(__name__)


async def run_startup_persistence(app: Any) -> None:
    """Run the three boot persistence steps; log + swallow so the task dies clean."""
    try:
        logger.info("startup persistence: ledger reconcile begin")
        await startup_ledger_reconcile(app)
        logger.info("startup persistence: closeout outbox replay begin")
        await startup_closeout_outbox_replay(app)
        logger.info("startup persistence: auto-job reconcile begin")
        await startup_auto_job_reconcile(app)
        logger.info("startup persistence: complete")
        app.state.startup_persistence_done = True
    except asyncio.CancelledError:
        app.state.startup_persistence_done = False
        raise
    except Exception:
        app.state.startup_persistence_done = False
        logger.exception("startup persistence failed (server already bound)")


def schedule_startup_persistence(app: Any) -> asyncio.Task[None]:
    """Schedule :func:`run_startup_persistence` and stash the task on ``app.state``."""
    app.state.startup_persistence_done = False
    task = asyncio.create_task(
        run_startup_persistence(app),
        name="giw-startup-persistence",
    )
    app.state.startup_persistence_task = task
    return task
