"""The ``PipelineExecutionTracker`` class — instance state and public surface.

Holds the in-process record store and exposes the thin public methods
(``register_execution``, ``get``, ``wait_for_terminal``, ``complete_execution``,
``fail_execution``, ``set_journal_writer``). The transition logic lives in the
sibling ``lifecycle`` / ``queries`` modules; the methods here are delegators so
the class stays a small state-plus-surface shell. See the package ``__init__``
module docstring for the dispatch-lifecycle invariants.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from transport_utils import DEFAULT_AGENT_BUS_URL

from . import lifecycle, queries
from .constants import _DEFAULT_MAX_RECORDS, _DEFAULT_RETENTION_SECONDS
from .protocol import _EventBusProtocol
from .records import PipelineExecutionRecord

if TYPE_CHECKING:
    import asyncio


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

    def set_journal_writer(
        self,
        journal_writer: Callable[[PipelineExecutionRecord], Awaitable[None]] | None,
    ) -> None:
        """Set/replace the terminal journal writer hook."""
        self._journal_writer = journal_writer

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
        bus_lifecycle: Literal["persistent", "ephemeral"] = "ephemeral",
        endpoint_request_id: str | None = None,
    ) -> PipelineExecutionRecord:
        """Admit a new execution and emit ``pipeline.dispatch.async``.

        Raises ``TrackerCapacityError`` (and emits ``pipeline.dispatch.rejected``)
        when saturation cannot be relieved by evicting a terminal record.
        """
        return lifecycle.register_execution(
            self,
            execution_id=execution_id,
            pipeline=pipeline,
            started_at=started_at,
            result_delivery=result_delivery,
            caller_agent=caller_agent,
            output_contract=output_contract,
            target_thread=target_thread,
            op=op,
            from_agent=from_agent,
            reply_subject=reply_subject,
            bus_lifecycle=bus_lifecycle,
            endpoint_request_id=endpoint_request_id,
        )

    def get(self, execution_id: str) -> PipelineExecutionRecord | None:
        """Return the record for ``execution_id`` or ``None`` if unknown/expired."""
        return queries.get_record(self, execution_id)

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
        return await queries.wait_for_terminal(self, execution_id, timeout_seconds)

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
        lifecycle.complete_execution(
            self,
            execution_id,
            content=content,
            model=model,
            model_entity_id=model_entity_id,
            usage=usage,
            duration_s=duration_s,
            reasoning=reasoning,
            hints=hints,
        )

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
        lifecycle.fail_execution(
            self,
            execution_id,
            code=code,
            message=message,
            data=data,
        )
