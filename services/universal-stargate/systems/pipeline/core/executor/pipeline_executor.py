"""``PipelineExecutor`` facade — sync + async entrypoints + instance helpers.

The class is the public surface (``execute`` for sync HTTP,
``execute_async`` for the async dispatch tracker). Method bodies that
exceed a few lines delegate to helper modules in this package; small
methods that own instance state (``_publish_event``,
``generate_execution_id``, ``_build_chat_completion_response``) keep
their bodies here.

Invariants:
- ``generate_execution_id()`` is called exactly once per dispatch; the
  minted id is threaded through ``prepare_execution`` so sync + async
  paths share identity with the DAG.
- Sync ``execute()`` MUST return a Response carrying the
  ``X-Pipeline-Execution-Id`` header (enforced by ``ResponseBuilder``).
- ``_publish_event`` mutates ``self._event_bus_warned`` (once-only
  warning when the event bus is unavailable) — kept on the class for
  this reason.
- Only ``DAGExecutor`` writes to ``context.outputs``; the facade and
  helper modules read but never mutate.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from fastapi.responses import Response
from universal_event_bus import Event
from universal_logging import get_logger

from ...registry import PipelineRegistry
from ..execution.concurrency_backend import (
    ConcurrencyBackend,
    InProcessConcurrencyBackend,
)
from ..execution.map_reduce.map_executor.events import ProxyProtocol
from ..execution.outcome import PipelineExecutionOutcome
from ..fragments import get_fragment_loader
from ..handlers import PipelineContext, StepOutput
from ..prompts import get_prompt_builder
from .exception_mapping import _normalize_pipeline_exception
from .execution_loop import run_prepared_execution, run_prepared_execution_inner
from .input_extraction import extract_messages, extract_source_text
from .output_resolution import (
    extract_backtranslation_data,
    extract_output_hints,
    get_final_result,
)
from .preparation import do_prepare_execution, expand_steps, extract_runtime_options
from .prepared import (
    PreparedPipelineExecution,
    _PipelineRequestContextProtocol,
    _RequestExecutorProtocol,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..execution.async_tracker import PipelineExecutionTracker
    from ..schemas import FragmentRef, PipelineSpec, StepConfig

logger = get_logger(__name__)


class PipelineExecutor:
    """
    Execute pipeline workflows using Directed Acyclic Graph (DAG)-based scheduling.

    Steps execute as soon as their dependencies are satisfied.
    Independent steps automatically run in parallel.

    CONCURRENCY: Only DAGExecutor writes to context.outputs.
    Handlers return StepOutput; they never write directly.
    """

    def __init__(
        self,
        registry: PipelineRegistry,
        request_executor: _RequestExecutorProtocol,
        proxy: ProxyProtocol,
    ):
        self.registry = registry
        self.request_executor = request_executor
        self.proxy = proxy
        self.prompt_builder = get_prompt_builder()
        self.fragment_loader = get_fragment_loader()
        self._event_bus_warned: bool = False
        # Phase A closure (cortex-chat-openai): per-key FIFO
        # serialisation for pipelines that declare a ``concurrency:``
        # block. ``InProcessConcurrencyBackend`` wraps
        # ``FifoCapacityGate(limit=1)`` per resolved key with TTL
        # eviction on release-when-idle. Phase B / slice 1c swaps the
        # backend impl (distributed) without touching this site or
        # the YAML surface.
        self._concurrency_backend: ConcurrencyBackend = InProcessConcurrencyBackend()

    def _publish_event(self, context: PipelineContext, event: Event) -> None:
        """
        Publish pipeline lifecycle event to the context's event bus (fire-and-forget).

        Ensures observability of pipeline execution for monitoring and JSONL recorders.
        If the event bus is unavailable, logs a warning once and drops events so
        execution continues without monitoring (graceful degradation).
        """
        proxy = getattr(context, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            asyncio.create_task(event_bus.publish_nowait(event))
        elif not getattr(self, "_event_bus_warned", False):
            logger.warning("Event bus unavailable - events will not be published")
            self._event_bus_warned = True

    def generate_execution_id(self) -> str:
        """Mint a fresh execution_id prior to DAG preparation or run."""
        return str(uuid.uuid4())

    def prepare_execution(
        self,
        context: _PipelineRequestContextProtocol,
        *,
        execution_id: str,
    ) -> PreparedPipelineExecution:
        """Delegate to ``preparation.do_prepare_execution``."""
        return do_prepare_execution(self, context, execution_id=execution_id)

    def _extract_runtime_options(
        self,
        context: _PipelineRequestContextProtocol,
        pipeline: PipelineSpec,
    ) -> dict[str, Any]:
        """Delegate to ``preparation.extract_runtime_options``."""
        return extract_runtime_options(context, pipeline)

    async def _run_prepared_execution(
        self,
        prepared: PreparedPipelineExecution,
        *,
        monitor_disconnect: bool = True,
    ) -> PipelineExecutionOutcome:
        """Delegate to ``execution_loop.run_prepared_execution``."""
        return await run_prepared_execution(
            self, prepared, monitor_disconnect=monitor_disconnect
        )

    async def _run_prepared_execution_inner(
        self,
        prepared: PreparedPipelineExecution,
        *,
        monitor_disconnect: bool = True,
    ) -> PipelineExecutionOutcome:
        """Delegate to ``execution_loop.run_prepared_execution_inner``."""
        return await run_prepared_execution_inner(
            self, prepared, monitor_disconnect=monitor_disconnect
        )

    def _build_chat_completion_response(
        self,
        prepared: PreparedPipelineExecution,
        outcome: PipelineExecutionOutcome,
    ) -> Response:
        """Shape outcome into ``/v1/chat/completions`` Response.

        Preserves the ``X-Pipeline-Execution-Id`` header (set by
        ``ResponseBuilder``) — existing MCP sync callers depend on it.
        """
        from ...response_builder import (
            ResponseBuilder,
        )  # noqa: I001  # late import avoids cycle

        return ResponseBuilder.build_response(
            prepared.pipeline_context,
            outcome.content,
            prepared.pipeline,
            outcome.step_outputs,
            outcome.backtranslation,
            execution_order=outcome.execution_order,
        )

    async def execute(self, context: _PipelineRequestContextProtocol) -> Response:
        """
        Execute a pipeline using DAG-based scheduling (sync HTTP path).

        Returns an OpenAI-compatible chat completion ``Response`` with the
        ``X-Pipeline-Execution-Id`` header set.

        Terminal-passthrough streaming: when the terminal step's ``StepOutput``
        carries a non-None ``stream`` (set by the generate handler's streaming
        branch for stream-eligible single-step pipelines), surfaces the
        pipeline spec and step-output dict on ``context`` and returns a
        placeholder Response whose only payload is the
        ``X-Pipeline-Execution-Id`` header. The proxy lifecycle
        (``execute_pipeline_chat_completion``) detects the surfaced attributes
        and replaces the placeholder with a ``StreamingResponse`` that drives
        the chunk iterator. ``ResponseBuilder`` is intentionally NOT invoked
        on this branch — its ``_aggregate_tokens`` canary exists precisely to
        flag this code path being mis-selected. See
        ``plan:pipeline-terminal-passthrough-streaming`` Phase 4.
        """
        execution_id = self.generate_execution_id()
        prepared = self.prepare_execution(context, execution_id=execution_id)
        try:
            outcome = await self._run_prepared_execution(prepared)
            terminal = prepared.pipeline_context.outputs.get(prepared.pipeline.output)
            if isinstance(terminal, StepOutput) and terminal.stream is not None:
                context.pipeline_spec = prepared.pipeline  # type: ignore[attr-defined]
                context._pipeline_outputs = (  # type: ignore[attr-defined]
                    prepared.pipeline_context.outputs
                )
                return Response(
                    content=b"",
                    status_code=200,
                    media_type="application/json",
                    headers={
                        "X-Pipeline-Execution-Id": (
                            prepared.pipeline_context.execution_id
                        ),
                    },
                )
            return self._build_chat_completion_response(prepared, outcome)
        finally:
            prepared.recorder.close()

    async def execute_async(
        self,
        context: _PipelineRequestContextProtocol,
        *,
        execution_id: str,
        started_at: str,
        tracker: PipelineExecutionTracker,
    ) -> None:
        """Run the prepared DAG in the background and record terminal state.

        Swallows ``PipelineError``/generic exceptions into
        ``tracker.fail_execution``. Re-raises ``asyncio.CancelledError``
        after marking the record failed so supervisors still observe the
        cancellation.
        """
        del started_at  # stored on tracker record during register_execution
        prepared: PreparedPipelineExecution | None = None
        try:
            prepared = self.prepare_execution(context, execution_id=execution_id)
            outcome = await self._run_prepared_execution(
                prepared, monitor_disconnect=False
            )
            tracker.complete_execution(
                execution_id,
                content=outcome.content,
                model=outcome.model,
                model_entity_id=outcome.model_entity_id,
                usage=outcome.usage,
                duration_s=outcome.duration_s,
                reasoning=outcome.reasoning,
                hints=outcome.hints,
            )
        except asyncio.CancelledError:
            tracker.fail_execution(
                execution_id,
                code="pipeline_execution_cancelled",
                message="Pipeline execution cancelled (shutdown or explicit cancel).",
            )
            raise
        except BaseException as exc:  # noqa: BLE001 — boundary of background task
            code, message, data = _normalize_pipeline_exception(exc)
            logger.error(
                "Async pipeline execution failed: execution_id=%s, code=%s, error=%s",
                execution_id,
                code,
                message,
                exc_info=True,
            )
            tracker.fail_execution(execution_id, code=code, message=message, data=data)
        finally:
            if prepared is not None:
                prepared.recorder.close()

    def _expand_steps(
        self,
        steps: Sequence[StepConfig | FragmentRef | dict[str, Any]],
    ) -> list[StepConfig]:
        """Delegate to ``preparation.expand_steps``."""
        return expand_steps(self, steps)

    def _extract_output_hints(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        output_aliases: dict[str, str] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Delegate to ``output_resolution.extract_output_hints``."""
        return extract_output_hints(pipeline, context, output_aliases)

    def _get_final_result(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        output_aliases: dict[str, str] | None = None,
    ) -> str:
        """Delegate to ``output_resolution.get_final_result``."""
        return get_final_result(pipeline, context, output_aliases)

    def _extract_backtranslation_data(
        self,
        steps: list[StepConfig],
        context: PipelineContext,
    ) -> dict[str, Any] | None:
        """Delegate to ``output_resolution.extract_backtranslation_data``."""
        return extract_backtranslation_data(steps, context)

    def _extract_messages(
        self, context: _PipelineRequestContextProtocol
    ) -> list[dict[str, Any]] | None:
        """Delegate to ``input_extraction.extract_messages``."""
        return extract_messages(context)

    def _extract_source_text(self, context: _PipelineRequestContextProtocol) -> str:
        """Delegate to ``input_extraction.extract_source_text``."""
        return extract_source_text(context)
