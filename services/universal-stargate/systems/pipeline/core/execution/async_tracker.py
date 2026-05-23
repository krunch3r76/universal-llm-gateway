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
  Note: for ``op="to_thread"`` records, ``_run_delivery_with_outcome``
  may demote a ``completed`` record to ``failed`` after the on-behalf
  POST fails (architectural decision dispatch-to-thread-delivery-2026-05-22
  §2.1). The demote emits a second ``pipeline.dispatch.completed`` with
  ``status="failed"`` for the same execution_id. Consumers of the event
  signal should expect up to two emissions per to_thread execution and
  key off the latest ``status``.
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
from typing import TYPE_CHECKING, Any, Literal, Protocol

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
    model_entity_id: str | None = None
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
    # dispatch-surface-split Phase 1: output contract + op tracking
    output_contract: Literal["inline", "thread"] = "inline"
    # Mirrors result_delivery.bus_thread for op="to_thread"
    target_thread: str | None = None
    # None = no op discrimination supplied (direct pipeline callers)
    op: Literal["generate", "to_thread"] | None = None
    # ISO-8601 Z; populated when the system-on-behalf post lands on
    # target_thread. Field name preserved for tracker.to_dict back-compat;
    # semantics shifted from "observed reply" to "post completed" in the
    # to-thread delivery architectural fix (2026-05-22) — see
    # notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md.
    thread_reply_observed_at: str | None = None
    # Identity to post as for op="to_thread". Populated at admission from the
    # role (team_dispatch) or model identifier (frontier_dispatch). Reply
    # turns are posted from this agent to record.caller_agent (or a thread
    # fallback) by the delivery handler.
    from_agent: str | None = None
    # Caller-supplied subject for the on-behalf reply turn. None ⇒ auto-derive.
    reply_subject: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the shape returned by ``GET /api/v1/pipelines/executions/{id}``."""  # noqa: E501
        result_payload: dict[str, Any] | None = None
        if self.result is not None:
            result_payload = {
                "content": self.result.content,
                "model": self.result.model,
                "model_entity_id": self.result.model_entity_id,
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
            "output_contract": self.output_contract,
            "target_thread": self.target_thread,
            "op": self.op,
            "thread_reply_observed_at": self.thread_reply_observed_at,
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
        record has no delivery config (neither legacy ``result_delivery``
        nor ``op="to_thread"`` with a ``target_thread``), emit ``.skipped``
        once so observability can distinguish "no hook configured at startup"
        (silent) from "hook present but no delivery config on this record"
        (.skipped) from "hook failed" (.failed).

        Phase 2: wraps the delivery call in ``_run_delivery_with_outcome``
        so ``op="to_thread"`` timeout failures can demote a provisional
        ``completed`` record to ``failed`` after reply observation exhausts.
        """
        if self._delivery_sender is None:
            return
        # For bus-mode records, reply observation only makes sense when the model
        # itself completed. If the model failed, there is no agent reply to observe.
        if record.op == "to_thread" and record.status != "completed":
            return
        has_delivery_config = record.result_delivery is not None or (
            record.op == "to_thread" and record.target_thread is not None
        )
        if not has_delivery_config:
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
            task = asyncio.create_task(self._run_delivery_with_outcome(record))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to schedule dispatch delivery: %s", exc)

    async def _run_delivery_with_outcome(self, record: PipelineExecutionRecord) -> None:
        """Invoke delivery sender; demote record to failed on bus-mode delivery failure.

        For ``op="to_thread"`` dispatches the record reaches ``completed`` from
        the model-completion side, but the true success criterion is the
        system-on-behalf POST landing on the target thread (architectural
        fix 2026-05-22 — see
        ``notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md``).
        If the delivery sender returns
        ``DeliveryOutcome(status="failed", failure_reason=…)``, this method
        demotes the record from ``completed`` to ``failed`` and schedules
        journaling so the final journal entry reflects the actual outcome.

        For all other outcomes (delivered, skipped, non-bus-mode) the record
        is unchanged here — side-effects (events, mutations) are the delivery
        sender's responsibility.
        """
        from .async_tracker_delivery import DeliveryOutcome

        try:
            result = await self._delivery_sender(record)  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Delivery sender raised unexpectedly: %s", exc)
            result = None

        if not isinstance(result, DeliveryOutcome):
            # Legacy senders returned None; treat as delivered (no demotion).
            if record.op == "to_thread":
                self._schedule_journal(record)
            return

        if (
            result.status == "failed"
            and record.op == "to_thread"
            and record.status == "completed"
        ):
            failure_reason = result.failure_reason or "delivery_failed"
            record.status = "failed"
            record.error = PipelineExecutionError(
                code=failure_reason,
                message=(
                    f"On-behalf reply post to thread {record.target_thread!r} "
                    f"failed: {failure_reason}."
                ),
            )
            self._emit(
                PipelineDispatchCompleted(
                    pipeline_id=record.pipeline,
                    execution_id=record.execution_id,
                    status="failed",
                    duration_s=time.monotonic() - record.started_at_monotonic,
                    caller_agent=record.caller_agent,
                    op=record.op or "",
                    output_contract=record.output_contract,
                )
            )

        # Journal deferred from complete_execution for to_thread records.
        if record.op == "to_thread":
            self._schedule_journal(record)

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
        output_contract: Literal["inline", "thread"] = "inline",
        target_thread: str | None = None,
        op: Literal["generate", "to_thread"] | None = None,
        from_agent: str | None = None,
        reply_subject: str | None = None,
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
            output_contract=output_contract,
            target_thread=target_thread,
            op=op,
            from_agent=from_agent,
            reply_subject=reply_subject,
        )
        self.records[execution_id] = record

        has_delivery_hook = result_delivery is not None or (
            op == "to_thread" and target_thread is not None
        )
        self._emit(
            PipelineDispatchAsync(
                pipeline_id=pipeline,
                execution_id=execution_id,
                has_delivery_hook=has_delivery_hook,
                caller_agent=record.caller_agent,
                op=op or "",
                output_contract=output_contract,
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
        model_entity_id: str | None = None,
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
            model_entity_id=model_entity_id,
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
                op=record.op or "",
                output_contract=record.output_contract,
            )
        )
        self._schedule_delivery(record)
        # Phase 2: for op="to_thread" records, journal is deferred until after
        # reply observation completes (via _run_delivery_with_outcome). This
        # ensures exactly one journal entry per record, carrying the FINAL status
        # (either "completed" on reply observed, or "failed" on timeout).
        if record.op != "to_thread":
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
                op=record.op or "",
                output_contract=record.output_contract,
            )
        )
        self._schedule_delivery(record)
        self._schedule_journal(record)
