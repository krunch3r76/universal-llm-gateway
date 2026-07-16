"""Event-authoritative terminal wait for federation remote model loads (B1)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_TERMINAL_RESOLVE_SLACK_S = 2.0


class WallClockLoadTimeout(TimeoutError):
    """Wall-clock / backstop budget exhausted — not an inner forwarder timeout."""


class TerminalLoadOutcome(StrEnum):
    LOADED = "loaded"
    FAILED = "failed"
    STUCK = "stuck"


@dataclass(frozen=True, slots=True)
class TerminalLoadResolution:
    outcome: TerminalLoadOutcome
    error: str | None = None
    gateway_name: str | None = None
    payload: dict[str, Any] | None = None


def _model_matches(payload: dict[str, Any], routing_key: str) -> bool:
    model_id = payload.get("model_id")
    if not model_id:
        return False
    model_text = str(model_id)
    if model_text == routing_key or model_text.startswith(f"{routing_key}-"):
        return True
    # Lifecycle telemetry often emits the base id without -hybrid / context suffix
    return routing_key == model_text or routing_key.startswith(f"{model_text}-")


def _gateway_matches(payload: dict[str, Any], gateway_id: str, remote_id: str) -> bool:
    gateway_name = payload.get("gateway_name")
    if gateway_name and str(gateway_name) in {gateway_id, remote_id}:
        return True
    url = payload.get("url")
    if url and (gateway_id in str(url) or remote_id in str(url)):
        return True
    return gateway_name is None and payload.get("url") is None


async def wait_for_terminal_load_event(
    event_bus: Any,
    *,
    routing_key: str,
    gateway_id: str,
    remote_id: str,
    timeout_s: float,
) -> TerminalLoadResolution | None:
    """Subscribe once and wait up to timeout_s for a terminal lifecycle event."""
    if event_bus is None:
        return None

    from src.scheduling.events.model_lifecycle import (
        MODEL_LOAD_FAILED,
        MODEL_LOADED,
        MODEL_LOADING_STUCK,
    )

    loop = asyncio.get_running_loop()
    future: asyncio.Future[TerminalLoadResolution] = loop.create_future()
    subscriptions: list[Any] = []

    def _maybe_resolve(event: Any, outcome: TerminalLoadOutcome) -> None:
        if future.done():
            return
        payload = getattr(event, "payload", None) or {}
        if not _model_matches(payload, routing_key):
            return
        if not _gateway_matches(payload, gateway_id, remote_id):
            return
        future.set_result(
            TerminalLoadResolution(
                outcome=outcome,
                error=payload.get("error"),
                gateway_name=payload.get("gateway_name"),
                payload=dict(payload),
            )
        )

    async def on_loaded(event: Any) -> None:
        _maybe_resolve(event, TerminalLoadOutcome.LOADED)

    async def on_failed(event: Any) -> None:
        _maybe_resolve(event, TerminalLoadOutcome.FAILED)

    async def on_stuck(event: Any) -> None:
        _maybe_resolve(event, TerminalLoadOutcome.STUCK)

    try:
        subscriptions.append(event_bus.subscribe_async(MODEL_LOADED, on_loaded))
        subscriptions.append(event_bus.subscribe_async(MODEL_LOAD_FAILED, on_failed))
        subscriptions.append(
            event_bus.subscribe_async(MODEL_LOADING_STUCK, on_stuck)
        )
        return await asyncio.wait_for(future, timeout=timeout_s)
    except TimeoutError:
        return None
    finally:
        for subscription in subscriptions:
            subscription.unsubscribe()


async def race_remote_load_with_terminal_events(
    event_bus: Any,
    remote_call: asyncio.Task[Any],
    *,
    routing_key: str,
    gateway_id: str,
    remote_id: str,
    backstop_timeout_s: float,
) -> tuple[Any | None, TerminalLoadResolution | None]:
    """Race remote HTTP load against terminal lifecycle events until backstop."""
    terminal_waiter = asyncio.create_task(
        wait_for_terminal_load_event(
            event_bus,
            routing_key=routing_key,
            gateway_id=gateway_id,
            remote_id=remote_id,
            timeout_s=backstop_timeout_s,
        )
    )
    try:
        done, pending = await asyncio.wait(
            {remote_call, terminal_waiter},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=backstop_timeout_s,
        )
    except TimeoutError:
        done = set()
        pending = {remote_call, terminal_waiter}

    if not done:
        remote_call.cancel()
        terminal_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await remote_call
            await terminal_waiter
        return None, None

    if terminal_waiter in done:
        terminal = terminal_waiter.result()
        if terminal is not None and terminal.outcome != TerminalLoadOutcome.LOADED:
            remote_call.cancel()
            with suppress(asyncio.CancelledError):
                await remote_call
            return None, terminal
        if terminal is not None and terminal.outcome == TerminalLoadOutcome.LOADED:
            # LOADED-first must not fall through to (None, None) — that was the
            # live deepseek false LOAD_TIMEOUT (model.loaded then 504 at ~53s).
            return await _resolve_after_terminal_loaded(remote_call, terminal)

    if remote_call in done:
        if not terminal_waiter.done():
            terminal_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await terminal_waiter
        exc = remote_call.exception()
        if exc is not None:
            raise exc
        return remote_call.result(), None

    for task in pending:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return None, None


async def _resolve_after_terminal_loaded(
    remote_call: asyncio.Task[Any],
    terminal: TerminalLoadResolution,
) -> tuple[Any, TerminalLoadResolution]:
    """Treat model.loaded as success; prefer HTTP ok if already finished."""
    if remote_call.done():
        if not remote_call.cancelled():
            exc = remote_call.exception()
            if exc is None:
                return remote_call.result(), terminal
            logger.warning(
                "Remote load HTTP failed after model.loaded; treating load as ok",
                extra={
                    "error": str(exc),
                    "gateway_name": terminal.gateway_name,
                },
            )
        return {"status": "ok"}, terminal

    # Do not await a possibly-hung HTTP forwarder after telemetry already
    # reported loaded (live deepseek: LOADED then false wall-clock 504).
    remote_call.cancel()
    with suppress(asyncio.CancelledError):
        await remote_call
    return {"status": "ok"}, terminal


async def await_remote_load_with_wall_clock(
    *,
    remote_call: asyncio.Task[Any],
    event_bus: Any | None,
    routing_key: str,
    gateway_id: str,
    remote_id: str,
    backstop_timeout_s: float,
    model_label: str,
) -> tuple[Any | None, TerminalLoadResolution | None]:
    """Await remote load; wall-clock expiry raises WallClockLoadTimeout."""
    if event_bus is None:
        try:
            return await asyncio.wait_for(remote_call, timeout=backstop_timeout_s), None
        except TimeoutError as exc:
            # wait_for propagates the task's TimeoutError and also raises on
            # budget expiry. Only the latter is wall-clock; preserve inner.
            if remote_call.done() and not remote_call.cancelled():
                task_exc = remote_call.exception()
                if isinstance(task_exc, TimeoutError) and not isinstance(
                    task_exc, WallClockLoadTimeout
                ):
                    raise task_exc from exc
            raise WallClockLoadTimeout(
                f"Remote load for {model_label} on {gateway_id} exceeded "
                f"{backstop_timeout_s}s wall-clock"
            ) from exc

    result, terminal = await race_remote_load_with_terminal_events(
        event_bus,
        remote_call,
        routing_key=routing_key,
        gateway_id=gateway_id,
        remote_id=remote_id,
        backstop_timeout_s=backstop_timeout_s,
    )
    if result is not None or terminal is not None:
        return result, terminal
    raise WallClockLoadTimeout(
        f"Remote load for {model_label} on {gateway_id} exceeded "
        f"{backstop_timeout_s}s backstop"
    )
