from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

from universal_logging import get_logger

from systems.pipeline.core.execution.dispatch_journal import (
    initialize_schema,
    journal_terminal,
    prune_expired,
)

from .startup_task_supervision import schedule_supervised_task

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


def _start_dispatch_journal_prune_loop(
    proxy: StargateProxy,
    *,
    retention_seconds: float,
) -> None:
    """Run hourly sqlite-journal retention pruning in the background."""

    async def _dispatch_journal_prune_loop() -> None:
        while True:
            try:
                await asyncio.sleep(3600.0)
                await prune_expired(
                    retention_seconds,
                    event_bus=proxy.event_bus,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Dispatch journal prune failed: %s", exc)

    schedule_supervised_task(
        _dispatch_journal_prune_loop(),
        name="dispatch-journal-prune-loop",
    )


async def initialize_dispatch_journal(proxy: StargateProxy) -> None:
    """
    Initialize async pipeline dispatch terminal-record persistence and prune loop.

    No setup if tracker absent. initialize_schema before set_journal_writer.
    Prune sleeps hourly, uses supervised task.
    """
    tracker = getattr(proxy, "pipeline_dispatch_tracker", None)
    if tracker is not None:
        await initialize_schema()
        tracker.set_journal_writer(
            partial(
                journal_terminal,
                event_bus=proxy.event_bus,
            )
        )
        _start_dispatch_journal_prune_loop(
            proxy,
            retention_seconds=tracker.retention_seconds,
        )
        logger.info("✅ Dispatch journal initialized for terminal record persistence")
