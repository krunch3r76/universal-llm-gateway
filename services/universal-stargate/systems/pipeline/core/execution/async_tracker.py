"""Async pipeline execution tracker.

In-process record store for pipelines dispatched via
``POST /api/v1/pipelines/dispatch``. The tracker is the sole writer of
dispatch-lifecycle signals — route handlers and the background wrapper drive
transitions, but the signal emission is centralized here so observability
is independent of the caller.

Invariants:
- ∀ ``register_execution`` success: emit ``pipeline.dispatch.async`` once.
- ∀ terminal transition (``complete_execution`` / ``fail_execution``):
  emit ``pipeline.dispatch.completed`` exactly once (idempotent guard).
- ∀ admission refusal: emit ``pipeline.dispatch.rejected`` before raising.
- TTL pruning uses ``completed_at_monotonic`` — running records are never
  evicted by age alone, only by explicit admission-time capacity pressure
  against terminal records.
- Optional journal hook persists terminal records out-of-process without
  blocking tracker transitions.

Records are node-local and non-durable across Stargate restart. Callers that
require durable result delivery must use the ``result_delivery`` hook
rather than polling after a restart.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from ..events.dispatch import (
    PipelineDispatchAsync,
    PipelineDispatchCompleted,
    PipelineDispatchRejected,
    PipelineDispatchTrackerExpired,
)

if TYPE_CHECKING:
    from universal_event_bus import Event

logger = get_logger(__name__)


_DEFAULT_MAX_RECORDS = 256
# 24h — covers overnight dispatch ("fire at 11pm, collect at 9am") cleanly.
# Stargate restart invalidates the in-process tracker regardless, so TTL is
# not the reliability bound; weekend-scale retention requires persistent
# backing (phase 2+). Approved thread 617.
_DEFAULT_RETENTION_SECONDS = 86400.0


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 Z form."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _EventBusProtocol(Protocol):
    """Minimal event-bus surface used by the tracker (avoids tight coupling).

    Note: ``publish_nowait`` is ``async def`` on the real event bus — the
    name is misleading. The tracker wraps the call in ``asyncio.create_task``
    so ``_emit`` can stay sync while the coroutine actually runs.
    """

    async def publish_nowait(self, event: Event) -> Any: ...


class TrackerCapacityError(RuntimeError):
    """Raised when the tracker cannot admit a new execution without dropping an active one."""  # noqa: E501


@dataclass(slots=True, kw_only=True)
class PipelineExecutionResult:
    """Canonical success payload captured by the tracker."""

    content: str
    model: str
    usage: dict[str, Any] | None = None
    duration_s: float = 0.0
    # Reasoning trace from reasoning-capable models (OpenAI GPT-5.x, o-series,
    # etc.). Shape preserved from upstream — structured blocks or a flat
    # string. ``None`` when the pipeline produced no reasoning trace.
    reasoning: Any = None
    # Structured anomaly/advisory hints from the dispatch step. Populated when
    # detectors fire (e.g. output_short on provider degradation) so polling
    # callers can triage silent failures without consulting the event service.
    # Each entry has at minimum ``type`` and ``reason`` keys.
    hints: list[dict[str, Any]] | None = None


@dataclass(slots=True, kw_only=True)
class PipelineExecutionError:
    """Canonical failure payload captured by the tracker.

    ``data`` holds the structured upstream error body when the failure
    originated from a provider HTTP 4xx/5xx with a JSON body
    (``ProxyClientError.detail`` when it arrived as a dict). Callers
    inspect ``data`` for provider-specific diagnostics (OpenAI's
    ``{type, code, param, message}`` shape, Anthropic's
    ``{type, error:{type, message}}``, etc.) without the adapter layer
    having to flatten them to strings. ``None`` when the failure was
    not an upstream HTTP error or the body was not JSON.
    """

    code: str
    message: str
    data: dict[str, Any] | None = None


@dataclass(slots=True, kw_only=True)
class PipelineExecutionRecord:
    """Per-execution record retained by the tracker.

    Uses ``field(default_factory=...)`` for timestamp and event defaults so
    every record gets a fresh value (bare ``datetime.now`` defaults would
    evaluate once at class-definition time).
    """

    execution_id: str
    pipeline: str
    status: str  # "running" | "completed" | "failed"
    started_at: str
    started_at_monotonic: float
    completed_at: str | None = None
    completed_at_monotonic: float | None = None
    result: PipelineExecutionResult | None = None
    error: PipelineExecutionError | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    terminal_event: asyncio.Event = field(default_factory=asyncio.Event)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the shape returned by ``GET /api/v1/pipelines/executions/{id}``."""  # noqa: E501
        result_payload: dict[str, Any] | None = None
        if self.result is not None:
            result_payload = {
                "content": self.result.content,
                "model": self.result.model,
                "usage": self.result.usage,
                "duration_s": self.result.duration_s,
                "reasoning": self.result.reasoning,
                "hints": self.result.hints or [],
            }
        error_payload: dict[str, Any] | None = None
        if self.error is not None:
            error_payload = {
                "code": self.error.code,
                "message": self.error.message,
                # Structured upstream body when the failure was an HTTP
                # error with a JSON response. Callers treat absence as
                # "no structured data available", not as an error.
                "data": self.error.data,
            }
        return {
            "execution_id": self.execution_id,
            "pipeline": self.pipeline,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": result_payload,
            "error": error_payload,
            "caller_agent": self.caller_agent,
        }


class PipelineExecutionTracker:
    """In-process async-dispatch record store.

    ¬ uses ``asyncio.Lock`` / ``asyncio.Semaphore`` — the tracker only
    performs dict writes (GIL-atomic for our access pattern) plus per-record
    ``asyncio.Event.set()`` for server-side polling. The project forbids
    unnecessary locks; this pattern is intentional.
    """

    def __init__(
        self,
        event_bus: _EventBusProtocol | None = None,
        *,
        max_records: int = _DEFAULT_MAX_RECORDS,
        retention_seconds: float = _DEFAULT_RETENTION_SECONDS,
        delivery_sender: (
            Callable[[PipelineExecutionRecord], Awaitable[None]] | None
        ) = None,
        journal_writer: (
            Callable[[PipelineExecutionRecord], Awaitable[None]] | None
        ) = None,
        agent_bus_url: str = DEFAULT_AGENT_BUS_URL,
        agent_bus_token: str = "",
    ) -> None:
        self.event_bus = event_bus
        self.max_records = max_records
        self.retention_seconds = retention_seconds
        self.records: dict[str, PipelineExecutionRecord] = {}
        self._delivery_sender = delivery_sender
        self._journal_writer = journal_writer
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._agent_bus_url = agent_bus_url
        self._agent_bus_token = agent_bus_token

    def _emit(self, event: Event) -> None:
        """Fire-and-forget publish; drop silently if no bus is wired.

        ``publish_nowait`` is an async method on the real event bus (the
        name refers to not blocking on subscribers, not to being sync).
        Wrapping in ``asyncio.create_task`` lets ``_emit`` remain sync while
        the coroutine actually gets scheduled. All tracker call sites run in
        an async context, so a running loop is guaranteed.
        """
        if self.event_bus is None:
            return
        try:
            task = asyncio.create_task(self.event_bus.publish_nowait(event))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to publish dispatch event: %s", exc)

    def _schedule_delivery(self, record: PipelineExecutionRecord) -> None:
        """Schedule bus delivery for a freshly-terminal record.

        No-op when no sender is wired. When a sender is wired but the
        record has no ``result_delivery`` config, emit ``.skipped`` once
        so observability can distinguish "no hook configured at startup"
        (silent) from "hook present but no delivery config on this
        record" (.skipped) from "hook failed" (.failed).
        """
        if self._delivery_sender is None:
            return
        if record.result_delivery is None:
            from ..events.delivery import PipelineDispatchDeliverySkipped

            self._emit(
                PipelineDispatchDeliverySkipped(
                    pipeline_id=record.pipeline,
                    execution_id=record.execution_id,
                    reason="no_delivery_config",
                )
            )
            return
        try:
            task = asyncio.create_task(self._delivery_sender(record))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to schedule dispatch delivery: %s", exc)

    def set_journal_writer(
        self,
        journal_writer: Callable[[PipelineExecutionRecord], Awaitable[None]] | None,
    ) -> None:
        """Set/replace the terminal journal writer hook."""
        self._journal_writer = journal_writer

    def _schedule_journal(self, record: PipelineExecutionRecord) -> None:
        """Schedule sqlite journaling for a freshly-terminal record."""
        if self._journal_writer is None:
            return
        try:
            task = asyncio.create_task(self._journal_writer(record))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to schedule dispatch journaling: %s", exc)

    def _schedule_dispatch_admit(self, record: PipelineExecutionRecord) -> None:
        """Fire-and-forget POST /threads/{id}/dispatch-admit after register_execution.

        No-op when agent_bus_token is unset (disabled path). Failures emit
        mcp.agentbus.dispatch.admit.failed and are otherwise swallowed so
        the tracker admission path is never affected.
        """
        if not self._agent_bus_token:
            return
        try:
            task = asyncio.create_task(self._do_dispatch_admit(record))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to schedule dispatch-admit for %s: %s", record.execution_id, exc
            )

    async def _do_dispatch_admit(self, record: PipelineExecutionRecord) -> None:
        """POST dispatch-admit to agent-bus; emit failure event on error."""
        from ..events.delivery import AgentBusDispatchAdmitFailed

        delivery = record.result_delivery or {}
        thread = delivery.get("bus_thread", "")
        pipeline_id = record.pipeline

        payload = {
            "execution_id": record.execution_id,
            "pipeline_id": pipeline_id,
            "caller_agent": record.caller_agent,
        }
        try:
            async with make_async_client(self._agent_bus_url, timeout=10.0) as client:
                response = await client.post(
                    f"/threads/{thread}/dispatch-admit",
                    headers={"Authorization": f"Bearer {self._agent_bus_token}"},
                    json=payload,
                )
                if response.status_code not in (200, 201):
                    self._emit(
                        AgentBusDispatchAdmitFailed(
                            execution_id=record.execution_id,
                            thread=thread,
                            status_code=response.status_code,
                            error_preview=response.text[:200],
                        )
                    )
        except Exception as exc:
            self._emit(
                AgentBusDispatchAdmitFailed(
                    execution_id=record.execution_id,
                    thread=thread,
                    status_code=0,
                    error_preview=str(exc)[:200],
                )
            )

    def _prune_terminal_records(self) -> None:
        """Drop terminal records whose age exceeds ``retention_seconds``.

        Emits ``pipeline.dispatch.tracker.expired`` per pruned record so the
        rate of un-collected results is observable — informs whether the
        retention window is sufficient in practice.
        """
        now_monotonic = time.monotonic()
        expired: list[tuple[str, PipelineExecutionRecord, float]] = []
        for exec_id, record in self.records.items():
            if record.completed_at_monotonic is None:
                continue
            age = now_monotonic - record.completed_at_monotonic
            if age > self.retention_seconds:
                expired.append((exec_id, record, age))
        for exec_id, record, age in expired:
            self.records.pop(exec_id, None)
            self._emit(
                PipelineDispatchTrackerExpired(
                    pipeline_id=record.pipeline,
                    execution_id=exec_id,
                    status=record.status,
                    age_seconds=age,
                )
            )

    def register_execution(
        self,
        *,
        execution_id: str,
        pipeline: str,
        started_at: str,
        result_delivery: dict[str, Any] | None = None,
        caller_agent: str | None = None,
    ) -> PipelineExecutionRecord:
        """Admit a new execution and emit ``pipeline.dispatch.async``.

        Raises ``TrackerCapacityError`` (and emits ``pipeline.dispatch.rejected``)
        when saturation cannot be relieved by evicting a terminal record.
        """
        self._prune_terminal_records()

        if len(self.records) >= self.max_records:
            terminal_id = next(
                (
                    eid
                    for eid, rec in self.records.items()
                    if rec.status in {"completed", "failed"}
                ),
                None,
            )
            if terminal_id is not None:
                self.records.pop(terminal_id, None)
            else:
                self._emit(
                    PipelineDispatchRejected(
                        pipeline_id=pipeline,
                        reason="capacity_exhausted",
                    )
                )
                raise TrackerCapacityError(
                    "Async pipeline tracker capacity exhausted; all slots running"
                )

        record = PipelineExecutionRecord(
            execution_id=execution_id,
            pipeline=pipeline,
            status="running",
            started_at=started_at,
            started_at_monotonic=time.monotonic(),
            result_delivery=result_delivery,
            caller_agent=caller_agent,
        )
        self.records[execution_id] = record

        self._emit(
            PipelineDispatchAsync(
                pipeline_id=pipeline,
                execution_id=execution_id,
                has_delivery_hook=result_delivery is not None,
                caller_agent=record.caller_agent,
            )
        )

        if result_delivery and result_delivery.get("bus_thread"):
            self._schedule_dispatch_admit(record)

        return record

    def get(self, execution_id: str) -> PipelineExecutionRecord | None:
        """Return the record for ``execution_id`` or ``None`` if unknown/expired."""
        self._prune_terminal_records()
        return self.records.get(execution_id)

    async def wait_for_terminal(
        self,
        execution_id: str,
        timeout_seconds: float,
    ) -> PipelineExecutionRecord | None:
        """Wait up to ``timeout_seconds`` for the record to reach a terminal state.

        Returns the record whether or not the wait elapsed; callers inspect
        ``record.status`` to decide how to respond. Returns ``None`` when the
        execution_id is unknown.
        """
        record = self.records.get(execution_id)
        if record is None:
            return None
        if record.status in {"completed", "failed"}:
            return record
        if timeout_seconds <= 0:
            return record
        try:
            await asyncio.wait_for(
                record.terminal_event.wait(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            pass
        return record

    def complete_execution(
        self,
        execution_id: str,
        *,
        content: str,
        model: str,
        usage: dict[str, Any] | None,
        duration_s: float,
        reasoning: Any = None,
        hints: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record success terminal state (idempotent)."""
        record = self.records.get(execution_id)
        if record is None or record.status in {"completed", "failed"}:
            logger.warning(
                "Ignoring duplicate/unknown terminal update execution_id=%s",
                execution_id,
            )
            return
        record.status = "completed"
        record.completed_at = _utc_now_iso()
        record.completed_at_monotonic = time.monotonic()
        record.result = PipelineExecutionResult(
            content=content,
            model=model,
            usage=usage,
            duration_s=duration_s,
            reasoning=reasoning,
            hints=hints,
        )
        record.terminal_event.set()
        self._emit(
            PipelineDispatchCompleted(
                pipeline_id=record.pipeline,
                execution_id=execution_id,
                status="completed",
                duration_s=duration_s,
                caller_agent=record.caller_agent,
            )
        )
        self._schedule_delivery(record)
        self._schedule_journal(record)

    def fail_execution(
        self,
        execution_id: str,
        *,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Record failure terminal state (idempotent).

        ``data`` carries the structured upstream error body when the
        failure came from a provider HTTP 4xx/5xx with a JSON response.
        See ``PipelineExecutionError`` for the shape contract.
        """
        record = self.records.get(execution_id)
        if record is None or record.status in {"completed", "failed"}:
            logger.warning(
                "Ignoring duplicate/unknown terminal update execution_id=%s",
                execution_id,
            )
            return
        record.status = "failed"
        record.completed_at = _utc_now_iso()
        record.completed_at_monotonic = time.monotonic()
        duration = record.completed_at_monotonic - record.started_at_monotonic
        record.error = PipelineExecutionError(code=code, message=message, data=data)
        record.terminal_event.set()
        self._emit(
            PipelineDispatchCompleted(
                pipeline_id=record.pipeline,
                execution_id=execution_id,
                status="failed",
                duration_s=duration,
                caller_agent=record.caller_agent,
            )
        )
        self._schedule_delivery(record)
        self._schedule_journal(record)
