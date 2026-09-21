"""Per-connection subscribe consume loop — watermark advance, cost warn, reconnect."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from libs.event_store.client import subscribe_events
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.ulg.connection_watermarks import (
    ConnectionWatermarks,
    filter_key,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import query_sock
from scripts.model_manager.ui.dispatch_monitor.ulg.records import event_from_row
from scripts.model_manager.ui.dispatch_monitor.ulg.subscribe_filters import LIVE_FILTERS

_PAYLOAD_WARN_BYTES = 64_000

Handler = Callable[[EventRecord], Awaitable[None]]
TruncationHandler = Callable[[str, int | None, dict[str, Any]], Awaitable[None]]

_TRUNCATION_TYPES = frozenset({"replay.truncated", "events.replay.truncated"})
_META_TYPES = frozenset({"subscribed", "events.dropped.subscribe"})


@dataclass(frozen=True)
class ConsumeResult:
    truncated: bool
    events_applied: int


def _matches_filter(event: dict[str, Any], filt: dict[str, str]) -> bool:
    for key, pattern in filt.items():
        value = str(event.get(key, ""))
        if pattern.endswith("*"):
            if not value.startswith(pattern[:-1]):
                return False
        elif value != pattern:
            return False
    return True


def _is_truncation_notice(raw: dict[str, Any]) -> bool:
    msg_type = raw.get("type")
    if msg_type in _TRUNCATION_TYPES:
        return True
    return bool(raw.get("replay_truncated"))


def _payload_bytes(raw: dict[str, Any]) -> int:
    payload = raw.get("payload")
    if isinstance(payload, str):
        return len(payload.encode())
    if isinstance(payload, (bytes, bytearray)):
        return len(payload)
    if payload is None:
        return 0
    try:
        return len(json.dumps(payload, default=str).encode())
    except (TypeError, ValueError):
        return 0


async def consume_connection(
    raw_stream: AsyncIterator[dict[str, Any]],
    *,
    event_filter: dict[str, str],
    watermarks: ConnectionWatermarks,
    handler: Handler,
    on_truncated: TruncationHandler | None = None,
) -> ConsumeResult:
    """Fold one connection's stream. Gaps and large payloads warn; nothing is dropped."""
    key = filter_key(event_filter)
    resume_from = watermarks.get(key)
    warned = False
    payload_warned = False
    applied = 0
    saw_seq = False

    async def _warn(detail: dict[str, Any]) -> None:
        nonlocal warned
        warned = True
        if on_truncated is not None:
            await on_truncated(key, resume_from, detail)

    async for raw in raw_stream:
        if not isinstance(raw, dict):
            continue
        if _is_truncation_notice(raw):
            notice = dict(raw)
            notice.setdefault("reason", "replay_notice")
            await _warn(notice)
            continue
        msg_type = raw.get("type")
        if msg_type in _META_TYPES:
            continue

        seq = raw.get("seq")
        if (
            not saw_seq
            and isinstance(seq, int)
            and not isinstance(seq, bool)
            and resume_from is not None
            and seq > resume_from + 1
        ):
            await _warn({"reason": "seq_gap", "first_seq": seq})

        size = _payload_bytes(raw)
        if size >= _PAYLOAD_WARN_BYTES and not payload_warned:
            payload_warned = True
            await _warn({"reason": "payload_costly", "payload_bytes": size})

        if not _matches_filter(raw, event_filter):
            continue

        event = event_from_row(raw)
        if event is None:
            continue

        if isinstance(seq, int) and not isinstance(seq, bool):
            saw_seq = True

        await handler(event)
        watermarks.advance(key, event.seq if event.seq is not None else seq)
        applied += 1

    return ConsumeResult(truncated=warned, events_applied=applied)


async def _connection_loop(
    event_filter: dict[str, str],
    handler: Handler,
    *,
    watermarks: ConnectionWatermarks,
    on_truncated: TruncationHandler,
    sock: str,
    backoff_s: float,
) -> None:
    delay = backoff_s
    key = filter_key(event_filter)
    while True:
        try:
            resume_from = watermarks.get(key)

            async def _stream() -> AsyncIterator[dict[str, Any]]:
                async for raw in subscribe_events(
                    sock, filter=event_filter, resume_from=resume_from
                ):
                    yield raw

            await consume_connection(
                _stream(),
                event_filter=event_filter,
                watermarks=watermarks,
                handler=handler,
                on_truncated=on_truncated,
            )
            delay = backoff_s
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


async def run_live_subscribers(
    handler: Handler,
    *,
    watermarks: ConnectionWatermarks,
    on_truncated: TruncationHandler,
    sock: str | None = None,
    backoff_s: float = 1.0,
) -> None:
    """Subscribe on all monitor filters until cancelled; reconnect with per-filter resume."""
    path = sock or query_sock()
    tasks = [
        asyncio.create_task(
            _connection_loop(
                event_filter,
                handler,
                watermarks=watermarks,
                on_truncated=on_truncated,
                sock=path,
                backoff_s=backoff_s,
            )
        )
        for event_filter in LIVE_FILTERS
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
