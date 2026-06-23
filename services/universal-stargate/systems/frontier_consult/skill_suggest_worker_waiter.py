"""Event-driven skill-suggest worker completion waiter (Layers 1–3)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from transport_utils import EVENTS_QUERY_SOCK
from universal_logging import get_logger

from .cursor_sdk_generate import CURSOR_SDK_REPLY_SEAT
from .skill_suggest_dispatch_config import SkillSuggestDispatchConfig
from .skill_suggest_durable_state import (
    DurableTerminalEvent,
    TERMINAL_SIGNALS,
    durable_catch_up_terminal,
    durable_idle_seconds,
    read_ledger_dispatch_row,
)

logger = get_logger(__name__)

WaitOutcomeKind = Literal[
    "completed",
    "failed",
    "delivery_failed",
    "timeout",
    "idle_timeout",
]


@dataclass(frozen=True, slots=True)
class WorkerWaitOutcome:
    kind: WaitOutcomeKind
    terminal: DurableTerminalEvent | None = None


class _WaitRegistration:
    __slots__ = ("execution_id", "dispatch_id", "thread_id", "event", "terminal")

    def __init__(
        self,
        *,
        execution_id: str,
        dispatch_id: str | None,
        thread_id: str,
    ) -> None:
        self.execution_id = execution_id
        self.dispatch_id = dispatch_id
        self.thread_id = thread_id
        self.event = asyncio.Event()
        self.terminal: DurableTerminalEvent | None = None


class SkillSuggestWorkerCompletionWaiter:
    """In-process waiter registry with optional live event-service subscription."""

    def __init__(self) -> None:
        self._registrations: dict[str, _WaitRegistration] = {}
        self._listener_task: asyncio.Task[None] | None = None
        self._listener_refs = 0

    def _notify(
        self,
        *,
        execution_id: str,
        thread_id: str,
        dispatch_id: str | None,
        terminal: DurableTerminalEvent,
    ) -> None:
        reg = self._registrations.get(execution_id)
        if reg is None:
            return
        if reg.thread_id != thread_id:
            return
        if dispatch_id and reg.dispatch_id and reg.dispatch_id != dispatch_id:
            return
        reg.terminal = terminal
        reg.event.set()

    def register(
        self,
        *,
        execution_id: str,
        dispatch_id: str | None,
        thread_id: str,
    ) -> _WaitRegistration:
        reg = _WaitRegistration(
            execution_id=execution_id,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
        self._registrations[execution_id] = reg
        return reg

    def unregister(self, execution_id: str) -> None:
        self._registrations.pop(execution_id, None)

    async def _ensure_listener(self) -> None:
        if self._listener_task is not None and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        from event_store.client import subscribe_events

        while self._registrations:
            try:
                async for raw in subscribe_events(
                    EVENTS_QUERY_SOCK,
                    filter={"signal": "frontier.sdk.worker.*"},
                ):
                    signal = str(raw.get("signal") or "")
                    if signal not in TERMINAL_SIGNALS:
                        continue
                    payload = raw.get("payload")
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            payload = {}
                    if not isinstance(payload, dict):
                        payload = {}
                    execution_id = str(
                        raw.get("execution_id") or payload.get("execution_id") or ""
                    )
                    thread_id = str(payload.get("thread_id") or "")
                    dispatch_id = str(payload.get("dispatch_id") or "") or None
                    if not execution_id or not thread_id:
                        continue
                    terminal = DurableTerminalEvent(
                        signal=signal,  # type: ignore[arg-type]
                        dispatch_id=dispatch_id,
                        thread_id=thread_id,
                        execution_id=execution_id,
                        payload=payload,
                    )
                    self._notify(
                        execution_id=execution_id,
                        thread_id=thread_id,
                        dispatch_id=dispatch_id,
                        terminal=terminal,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("skill_suggest worker listener error: %s", exc)
                await asyncio.sleep(1.0)

    async def wait_for_terminal(
        self,
        *,
        execution_id: str,
        dispatch_id: str | None,
        thread_id: str,
        config: SkillSuggestDispatchConfig,
    ) -> WorkerWaitOutcome:
        reg = self.register(
            execution_id=execution_id,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
        self._listener_refs += 1
        await self._ensure_listener()
        try:
            catch_up = durable_catch_up_terminal(
                execution_id=execution_id,
                thread_id=thread_id,
                dispatch_id=dispatch_id,
            )
            if catch_up is not None:
                return _outcome_from_terminal(catch_up)

            while True:
                try:
                    await asyncio.wait_for(
                        reg.event.wait(),
                        timeout=config.idle_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass

                catch_up = durable_catch_up_terminal(
                    execution_id=execution_id,
                    thread_id=thread_id,
                    dispatch_id=dispatch_id,
                )
                if catch_up is not None:
                    return _outcome_from_terminal(catch_up)

                if reg.terminal is not None:
                    return _outcome_from_terminal(reg.terminal)

                ledger = read_ledger_dispatch_row(
                    dispatch_id=dispatch_id,
                    execution_id=execution_id,
                    thread_id=thread_id,
                )
                idle_s = durable_idle_seconds(ledger)
                if idle_s is not None and idle_s > config.idle_timeout_seconds:
                    return WorkerWaitOutcome(kind="idle_timeout", terminal=None)
        finally:
            self.unregister(execution_id)
            self._listener_refs -= 1
            if self._listener_refs <= 0 and self._listener_task is not None:
                self._listener_task.cancel()
                self._listener_task = None


def _outcome_from_terminal(terminal: DurableTerminalEvent) -> WorkerWaitOutcome:
    if terminal.signal == "frontier.sdk.worker.completed":
        return WorkerWaitOutcome(kind="completed", terminal=terminal)
    if terminal.signal == "frontier.sdk.worker.delivery_failed":
        return WorkerWaitOutcome(kind="delivery_failed", terminal=terminal)
    if terminal.signal == "frontier.sdk.worker.timeout":
        return WorkerWaitOutcome(kind="timeout", terminal=terminal)
    return WorkerWaitOutcome(kind="failed", terminal=terminal)


_WAITER = SkillSuggestWorkerCompletionWaiter()


async def await_worker_completion(
    *,
    execution_id: str,
    dispatch_id: str | None,
    thread_id: str,
    config: SkillSuggestDispatchConfig,
) -> WorkerWaitOutcome:
    return await _WAITER.wait_for_terminal(
        execution_id=execution_id,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        config=config,
    )


def reset_worker_completion_waiter_for_tests() -> None:
    _WAITER._registrations.clear()
    if _WAITER._listener_task is not None:
        _WAITER._listener_task.cancel()
        _WAITER._listener_task = None
    _WAITER._listener_refs = 0
