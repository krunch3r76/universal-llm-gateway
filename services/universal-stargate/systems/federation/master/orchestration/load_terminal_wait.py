"""Event-authoritative terminal wait for federation remote model loads (B1 + B2)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_TERMINAL_RESOLVE_SLACK_S = 2.0


class WallClockLoadTimeout(TimeoutError):  # noqa: N818
    """Wall-clock / backstop budget exhausted — not an inner forwarder timeout."""


@dataclass(frozen=True, slots=True)
class IdleLoadTimeoutInfo:
    """Structured idle-timeout payload for LOAD_TIMEOUT envelopes."""

    idle_seconds: float
    last_event: dict[str, Any]
    idle_budget_s: float


class IdleLoadTimeout(TimeoutError):  # noqa: N818
    """Progress silence exceeded idle_budget — not a completion deadline."""

    def __init__(self, info: IdleLoadTimeoutInfo, message: str) -> None:
        self.info = info
        super().__init__(message)


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


def _progress_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal": "model.loading.progress",
        "model_id": payload.get("model_id"),
        "phase": payload.get("phase"),
        "pct": payload.get("pct"),
        "gateway_name": payload.get("gateway_name"),
        "url": payload.get("url"),
    }


class _ProgressTracker:
    """Mutable progress/idle state for one active remote load wait."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._last_progress_at: float | None = None
        self._last_event: dict[str, Any] | None = None
        self._first_progress = asyncio.Event()
        self._progress_updated = asyncio.Event()

    def record(self, payload: dict[str, Any]) -> None:
        self._last_progress_at = self._loop.time()
        self._last_event = _progress_payload_summary(payload)
        self._first_progress.set()
        self._progress_updated.set()

    @property
    def last_event(self) -> dict[str, Any] | None:
        return self._last_event

    def idle_seconds(self) -> float:
        if self._last_progress_at is None:
            return 0.0
        return self._loop.time() - self._last_progress_at

    async def wait_for_first_progress(self) -> None:
        await self._first_progress.wait()

    async def wait_for_progress_update(self, timeout: float) -> bool:
        self._progress_updated.clear()
        try:
            await asyncio.wait_for(self._progress_updated.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False


async def _wait_for_idle_timeout(
    tracker: _ProgressTracker,
    idle_budget_s: float,
) -> IdleLoadTimeoutInfo:
    """Fire after progress silence reaches idle_budget (requires prior progress)."""
    await tracker.wait_for_first_progress()
    while True:
        remaining = idle_budget_s - tracker.idle_seconds()
        if remaining <= 0:
            break
        if not await tracker.wait_for_progress_update(remaining):
            break
    last_event = tracker.last_event or {}
    return IdleLoadTimeoutInfo(
        idle_seconds=tracker.idle_seconds(),
        last_event=last_event,
        idle_budget_s=idle_budget_s,
    )


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
    idle_budget_s: float | None = None,
) -> tuple[Any | None, TerminalLoadResolution | None]:
    """Race remote HTTP load against terminal lifecycle events until backstop."""
    progress_tracker: _ProgressTracker | None = None
    idle_task: asyncio.Task[IdleLoadTimeoutInfo] | None = None
    progress_subscription: Any | None = None

    if event_bus is not None and idle_budget_s is not None:
        from src.scheduling.events.model_lifecycle import MODEL_LOADING_PROGRESS

        loop = asyncio.get_running_loop()
        progress_tracker = _ProgressTracker(loop)

        async def on_progress(event: Any) -> None:
            if progress_tracker is None:
                return
            payload = getattr(event, "payload", None) or {}
            if not _model_matches(payload, routing_key):
                return
            if not _gateway_matches(payload, gateway_id, remote_id):
                return
            phase = payload.get("phase")
            pct = payload.get("pct")
            if not phase or pct is None:
                return
            progress_tracker.record(payload)

        progress_subscription = event_bus.subscribe_async(
            MODEL_LOADING_PROGRESS, on_progress
        )
        idle_task = asyncio.create_task(
            _wait_for_idle_timeout(progress_tracker, idle_budget_s)
        )

    terminal_waiter = asyncio.create_task(
        wait_for_terminal_load_event(
            event_bus,
            routing_key=routing_key,
            gateway_id=gateway_id,
            remote_id=remote_id,
            timeout_s=backstop_timeout_s,
        )
    )
    wait_set: set[asyncio.Task[Any]] = {remote_call, terminal_waiter}
    if idle_task is not None:
        wait_set.add(idle_task)

    try:
        try:
            done, pending = await asyncio.wait(
                wait_set,
                return_when=asyncio.FIRST_COMPLETED,
                timeout=backstop_timeout_s,
            )
        except TimeoutError:
            done = set()
            pending = wait_set.copy()

        if idle_task is not None and idle_task in done:
            idle_info = idle_task.result()
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            raise IdleLoadTimeout(
                idle_info,
                f"Remote load progress silence exceeded {idle_info.idle_budget_s}s "
                f"(idle {idle_info.idle_seconds:.1f}s)",
            )

        if not done:
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            return None, None

        if terminal_waiter in done:
            terminal = terminal_waiter.result()
            if terminal is not None and terminal.outcome != TerminalLoadOutcome.LOADED:
                remote_call.cancel()
                with suppress(asyncio.CancelledError):
                    await remote_call
                return None, terminal
            if terminal is not None and terminal.outcome == TerminalLoadOutcome.LOADED:
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
    finally:
        if idle_task is not None and not idle_task.done():
            idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await idle_task
        if progress_subscription is not None:
            progress_subscription.unsubscribe()


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
    idle_budget_s: float | None = None,
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

    try:
        result, terminal = await race_remote_load_with_terminal_events(
            event_bus,
            remote_call,
            routing_key=routing_key,
            gateway_id=gateway_id,
            remote_id=remote_id,
            backstop_timeout_s=backstop_timeout_s,
            idle_budget_s=idle_budget_s,
        )
    except IdleLoadTimeout:
        raise

    if result is not None or terminal is not None:
        return result, terminal
    raise WallClockLoadTimeout(
        f"Remote load for {model_label} on {gateway_id} exceeded "
        f"{backstop_timeout_s}s backstop"
    )
