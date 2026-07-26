"""Async multi-filter Event Service subscriber feeding a shared handler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from libs.event_store.client import subscribe_events

from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import query_sock
from scripts.model_manager.ui.dispatch_monitor.ulg.records import event_from_row

LIVE_FILTERS: tuple[dict[str, str], ...] = (
    {"signal": "manage.charter.tick.*"},
    {"signal": "frontier.sdk.*"},
    {"signal": "cdp.generate.*"},
    {"signal": "frontier.poll.hint.issued"},
)

Handler = Callable[[EventRecord], Awaitable[None]]


async def _consume_filter(
    event_filter: dict[str, str],
    handler: Handler,
    *,
    resume_from: int | None,
    sock: str,
) -> None:
    async for raw in subscribe_events(sock, filter=event_filter, resume_from=resume_from):
        if not isinstance(raw, dict):
            continue
        if raw.get("type") in {"subscribed", "events.dropped.subscribe"}:
            continue
        event = event_from_row(raw)
        if event is not None:
            await handler(event)


async def run_live_subscribers(
    handler: Handler,
    *,
    resume_from: int | None = None,
    sock: str | None = None,
) -> None:
    """Subscribe on all monitor filters until cancelled."""
    path = sock or query_sock()
    tasks = [
        asyncio.create_task(
            _consume_filter(event_filter, handler, resume_from=resume_from, sock=path)
        )
        for event_filter in LIVE_FILTERS
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
