"""
Pipeline executor with DAG-based scheduling.

Features:
- Automatic parallelization based on dependencies
- Conditional step execution
- Fragment expansion
- Domain-aware handler dispatch
- Single-writer to context.outputs (DAGExecutor only)
- Split sync/async entry points sharing prepared state

Invariants:
- ∀ step: dependencies complete before execution
- Parallel steps do not share mutable state
- First failure propagates immediately (fail-fast)
- Only DAGExecutor writes to context.outputs
- ``generate_execution_id()`` is called exactly once per dispatch; the
  minted id is threaded through ``prepare_execution`` so sync + async paths
  share identity with the DAG.
- Sync ``execute()`` MUST return a Response carrying the
  ``X-Pipeline-Execution-Id`` header (enforced by ``ResponseBuilder``).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from fastapi.responses import Response
from universal_event_bus import Event
from universal_logging import get_logger

from ..registry import PipelineRegistry
from .dag import DAGBuilder, StepState
from .events import EventRecorder
from .events.lifecycle import (
    PipelineCancelled,
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
)
from .events.pipeline import (
    PipelineCancelled as BusPipelineCancelled,
)
from .events.pipeline import (
    PipelineCompleted as BusPipelineCompleted,
)
from .events.pipeline import (
    PipelineFailed as BusPipelineFailed,
)
from .events.pipeline import (
    PipelineStarted as BusPipelineStarted,
)
from .events.step import (
    SubPipelineExpanded as BusSubPipelineExpanded,
)
from .execution import DAGExecutor
from .execution.concurrency import maybe_concurrency_lock
from .execution.disconnect_monitor import execute_with_disconnect_monitoring
from .execution.map_reduce.map_executor.events import ProxyProtocol
from .execution.outcome import PipelineExecutionOutcome, extract_model_entity_id
from .fragments import get_fragment_loader
from .handlers import PipelineContext, StepOutput
from .prompts import get_prompt_builder
from .schemas import FragmentRef, PipelineSpec, StepConfig

if TYPE_CHECKING:
    from .execution.async_tracker import PipelineExecutionTracker


class _RequestExecutorProtocol(Protocol):
    """Minimal contract for request execution (avoids importing proxy at runtime)."""

    async def execute_request(self, context: Any) -> Any: ...


class _PipelineRequestContextProtocol(Protocol):
    """Context passed to execute(); has http_request, original_request, chat_request."""

    http_request: Any
    original_request: dict[str, Any] | None
    chat_request: Any


logger = get_logger(__name__)
execution_logger = get_logger("systems.pipeline.execution")


@dataclass(slots=True, kw_only=True)
class PreparedPipelineExecution:
    """Shared prepared state consumed by sync and async pipeline entrypoints.

    Holds the resolved pipeline spec, DAG context, node map, extracted input
    text, and DAG executor so both ``/v1/chat/completions`` and
    ``/api/v1/pipelines/dispatch`` execute the same DAG without duplicating
    setup or re-parsing results.
    """

    pipeline: PipelineSpec
    pipeline_context: PipelineContext
    nodes: dict[str, Any]
    steps: list[StepConfig]
    output_aliases: dict[str, str]
    text: str
    execution_id: str
    dag_executor: DAGExecutor
    recorder: EventRecorder
    start_monotonic: float = field(default=0.0)


def _extract_chat_id(context: _PipelineRequestContextProtocol) -> str | None:
    """Lift ``chat_id`` from ``context.original_request`` for persistent chat
    pipelines (e.g. ``cortex-chat-openai``).

    Returns the stripped string when present and non-empty; ``None`` otherwise.
    Non-string payloads silently coerce to ``None`` — the pipeline definition
    decides whether absence is a hard error via step-level validation.
    """
    if not context.original_request:
        return None
    raw = context.original_request.get("chat_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _normalize_pipeline_exception(
    exc: BaseException,
) -> tuple[str, str, dict[str, Any] | None]:
    """Map known pipeline exceptions to ``(code, message, data)``.

    - ``code`` / ``message`` come from ``to_dict()`` when the exception
      provides one (e.g. ``PipelineError``), else from ``str(exc)``.
    - ``data`` carries the structured upstream body when the exception is
      a ``ProxyClientError`` with a dict-shaped ``detail`` — that path
      preserves provider HTTP 4xx/5xx JSON without flattening to a string.
    - Returns ``data=None`` for non-HTTP exceptions or when the upstream
      body was not JSON.
    """
    data: dict[str, Any] | None = None

    # ProxyClientError.detail is the structured upstream JSON body when
    # the provider returned a parseable error response. Surface it so
    # async callers can inspect {type, code, param, message} without
    # re-parsing a flattened error string.
    proxy_detail = getattr(exc, "detail", None)
    if isinstance(proxy_detail, dict):
        data = proxy_detail

    # Step-level wrappers (e.g. ``PipelineExecutionError`` raised by the DAG
    # executor) lack ``to_dict`` but preserve the originating step exception
    # as ``__cause__``. Walk the cause chain so structured ``PipelineError``
    # subclasses (e.g. ``RemoteMcpUnsupportedError``) surface their ``code``
    # to the final error envelope rather than collapsing to the generic
    # ``pipeline_execution_failed`` fallback.
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__

    for candidate in chain:
        if not hasattr(candidate, "to_dict"):
            continue
        try:
            payload = candidate.to_dict()
        except Exception:  # noqa: BLE001 — upstream exc shape varies
            payload = None
        if isinstance(payload, dict):
            code = str(
                payload.get("code")
                or payload.get("error_type")
                or "pipeline_execution_failed"
            )
            message = str(payload.get("message") or payload.get("error") or candidate)
            return code, message, data or payload
    return "pipeline_execution_failed", str(exc), data


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
        # Phase 5 (cortex-chat-openai): per-key serialisation locks for
        # pipelines that declare a ``concurrency:`` block. Plain dict
        # leaks one Lock per distinct resolved key for the process
        # lifetime; acceptable at Phase A scale (master Stargate
        # restart cadence evicts). Substrate-level cleanup promotes to
        # Phase B without YAML or pipeline-author impact.
        self._concurrency_locks: dict[str, asyncio.Lock] = {}

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
        """Resolve pipeline spec, build DAG context/nodes, extract input text.

        Performs all synchronous setup prior to DAG execution: registry
        lookup, fragment expansion, DAG construction, context creation,
        dependency injection, and initial event emission. Emits
        ``PipelineStarted`` on both the JSONL recorder and the event bus.
        """
        pipeline = self.registry.get_pipeline(context.selected_model)

        logger.info(
            f"Executing pipeline '{pipeline.id}' "
            f"(version {pipeline.version}, type: {pipeline.type})"
        )

        text = self._extract_source_text(context)
        messages = self._extract_messages(context)

        if not context.original_request:
            logger.error(
                f"Pipeline '{pipeline.id}': original_request missing in context. "
                f"Cannot generate execution summary."
            )
        elif not context.original_request.get("messages"):
            logger.warning(
                f"Pipeline '{pipeline.id}': original_request has no messages. "
                f"Execution summary will not include conversation history."
            )

        if pipeline.fragments:
            self.fragment_loader.register_inline_fragments(pipeline.fragments)

        steps = self._expand_steps(pipeline.steps)

        dag_builder = DAGBuilder(steps)
        nodes = dag_builder.build()
        output_aliases = dict(dag_builder.output_aliases or {})

        ready_count = sum(1 for n in nodes.values() if not n.dependencies)
        logger.info(
            f"Pipeline '{pipeline.id}' DAG: {len(nodes)} nodes, "
            f"{ready_count} ready for parallel execution"
        )

        runtime_options = self._extract_runtime_options(context, pipeline)

        if pipeline.id == "rag-context" and "corpus_hints" not in runtime_options:
            try:
                from pipelines.rag.corpus_hints_loader import fetch_corpus_hints_text

                runtime_options = dict(runtime_options)
                runtime_options["corpus_hints"] = fetch_corpus_hints_text()
            except Exception as e:
                logger.debug(
                    "Pipeline '%s': could not load corpus hints: %s",
                    pipeline.id,
                    e,
                )

        pipeline_context = PipelineContext(
            pipeline=pipeline,
            source_text=text,
            http_request=context.http_request,
            execution_id=execution_id,
            runtime_options=runtime_options,
            _messages=messages,
            chat_id=_extract_chat_id(context),
        )

        if runtime_options:
            merged_overrides = pipeline_context.options.get("model_ref_overrides")
            mo_repr = (
                dict(merged_overrides)
                if isinstance(merged_overrides, dict)
                else merged_overrides
            )
            logger.info(
                "Pipeline '%s': context.options.model_ref_overrides = %s",
                pipeline.id,
                mo_repr,
            )

        execution_logger.info(
            f"Pipeline execution started: pipeline={pipeline.id}, "
            f"execution_id={execution_id}, source_text='{text}'"
        )

        pipeline_context._registry = self.registry
        pipeline_context._request_executor = self.request_executor
        pipeline_context._proxy = self.proxy

        if output_aliases:
            for (
                parent_step_name,
                resolved_output_step,
            ) in output_aliases.items():
                prefix = f"{parent_step_name}__"
                expanded_count = sum(
                    1 for node_step_name in nodes if node_step_name.startswith(prefix)
                )
                self._publish_event(
                    pipeline_context,
                    BusSubPipelineExpanded(
                        pipeline_id=pipeline.id,
                        execution_id=pipeline_context.execution_id,
                        parent_step_name=parent_step_name,
                        resolved_output_step=resolved_output_step,
                        expanded_step_count=expanded_count,
                    ),
                )

        from pathlib import Path

        log_base = pipeline_context.options.get(
            "log_dir", "/tmp/logs/universal-stargate"
        )
        exec_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        exec_short = execution_id[:8]
        event_dir = (
            Path(log_base)
            / "pipeline_summaries"
            / pipeline.id
            / f"{exec_ts}_{exec_short}"
        )
        recorder = EventRecorder(
            pipeline_id=pipeline.id,
            execution_id=execution_id,
            output_dir=event_dir,
        )
        pipeline_context._recorder = recorder

        if (
            hasattr(context, "selected_gateway_instance")
            and context.selected_gateway_instance
        ):
            gateway_name = context.selected_gateway_instance.config.name
            pipeline_context.selected_gateway_instance = gateway_name

        recorder.emit(
            PipelineStarted(
                step_count=len(nodes),
                timeout_seconds=pipeline_context.options.get("timeout_seconds"),
                source_text=text,
            ),
        )
        self._publish_event(
            pipeline_context,
            BusPipelineStarted(
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                domain=pipeline.domain,
                step_count=len(nodes),
                timeout_seconds=pipeline_context.options.get("timeout_seconds"),
            ),
        )

        dag_executor = DAGExecutor(nodes, pipeline_context)

        return PreparedPipelineExecution(
            pipeline=pipeline,
            pipeline_context=pipeline_context,
            nodes=nodes,
            steps=steps,
            output_aliases=output_aliases,
            text=text,
            execution_id=execution_id,
            dag_executor=dag_executor,
            recorder=recorder,
            start_monotonic=time.time(),
        )

    def _extract_runtime_options(
        self,
        context: _PipelineRequestContextProtocol,
        pipeline: PipelineSpec,
    ) -> dict[str, Any]:
        """Flatten ``pipeline_options`` + merged ``model_ref_overrides`` from request."""  # noqa: E501
        runtime_options: dict[str, Any] = {}
        if not context.original_request:
            return runtime_options

        orig_keys = list(context.original_request.keys())
        raw_po = context.original_request.get("pipeline_options")
        if raw_po is None:
            po_flat: dict[str, Any] = {}
        elif not isinstance(raw_po, dict):
            raise ValueError(
                f"Invalid pipeline_options type: expected dict, "
                f"got {type(raw_po).__name__}"
            )
        else:
            po_flat = dict(raw_po)

        runtime_options = po_flat

        top_mro = context.original_request.get("model_ref_overrides")
        top_d = top_mro if isinstance(top_mro, dict) else {}
        inner_mro = runtime_options.get("model_ref_overrides")
        inner_d = inner_mro if isinstance(inner_mro, dict) else {}
        if top_d or inner_d:
            runtime_options["model_ref_overrides"] = {**top_d, **inner_d}

        if runtime_options:
            option_keys = list(runtime_options.keys())
            logger.info(
                f"Pipeline '{pipeline.id}': Received runtime options: {option_keys}"
            )
            merged_mro = runtime_options.get("model_ref_overrides")
            if isinstance(merged_mro, dict) and merged_mro:
                logger.info(
                    "Pipeline '%s': model_ref_overrides from request: %s",
                    pipeline.id,
                    dict(merged_mro),
                )
        elif "pipeline_options" not in context.original_request:
            logger.warning(
                (
                    "Pipeline '%s': original_request has no 'pipeline_options' "
                    "(keys: %s). model_ref_overrides empty unless set at top level."
                ),
                pipeline.id,
                orig_keys,
            )
        return runtime_options

    async def _run_prepared_execution(
        self,
        prepared: PreparedPipelineExecution,
        *,
        monitor_disconnect: bool = True,
    ) -> PipelineExecutionOutcome:
        """Acquire per-chat concurrency lock (if declared) and run the DAG.

        Thin wrapper that serialises pipeline executions on a resolved
        string key when ``pipeline.concurrency.key`` is declared in the
        pipeline YAML. No-op when the pipeline carries no
        ``concurrency:`` block — see ``execution.concurrency`` for the
        resolution rules and ``ConcurrencyLockTimeoutError`` for the
        timeout-failure shape.
        """
        async with maybe_concurrency_lock(
            prepared.pipeline,
            prepared.pipeline_context,
            self._concurrency_locks,
        ):
            return await self._run_prepared_execution_inner(
                prepared, monitor_disconnect=monitor_disconnect
            )

    async def _run_prepared_execution_inner(
        self,
        prepared: PreparedPipelineExecution,
        *,
        monitor_disconnect: bool = True,
    ) -> PipelineExecutionOutcome:
        """Execute the prepared DAG and return structured outcome.

        Emits ``PipelineCompleted``/``PipelineFailed``/``PipelineCancelled``
        on both the recorder and the event bus. On failure, re-raises the
        original exception with ``execution_id`` attached (preserving sync
        ``execute()`` contract). The recorder is flushed in the caller's
        ``finally`` block — this method does not close it.

        ``monitor_disconnect`` controls whether the execution races against
        a client-disconnection poller on ``pipeline_context.http_request``.
        Sync ``/v1/chat/completions`` callers hold a live connection for the
        duration of execution and want the cancel-on-disconnect ergonomics.
        Async ``/api/v1/pipelines/dispatch`` callers close the connection
        right after the 202 response — execution lifecycle is detached from
        the caller, so the monitor must be disabled (otherwise every
        non-trivial async run is cancelled at the first poll tick).
        """
        pipeline = prepared.pipeline
        pipeline_context = prepared.pipeline_context
        nodes = prepared.nodes
        dag_executor = prepared.dag_executor
        recorder = prepared.recorder

        start_time = prepared.start_monotonic or time.time()
        pipeline_timeout = float(pipeline_context.options.get("timeout_seconds", 60))

        try:
            if monitor_disconnect:
                execution_coro = execute_with_disconnect_monitoring(
                    dag_executor=dag_executor,
                    http_request=pipeline_context.http_request,
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    step_count=len(nodes),
                )
            else:
                execution_coro = dag_executor.execute()
            await asyncio.wait_for(execution_coro, timeout=pipeline_timeout)
            duration = time.time() - start_time

            recorder.emit(
                PipelineCompleted(
                    duration_ms=duration * 1000,
                    output_step=pipeline.output,
                ),
            )
            self._publish_event(
                pipeline_context,
                BusPipelineCompleted(
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    duration_seconds=duration,
                    step_count=len(nodes),
                    output_step=pipeline.output,
                ),
            )
        except TimeoutError:
            duration = time.time() - start_time
            await dag_executor.cancel()

            error_msg = f"Pipeline '{pipeline.id}' timed out after {pipeline_timeout}s"
            execution_logger.error(
                "Pipeline execution timed out: pipeline=%s, "
                "execution_id=%s, timeout=%ss, duration=%.2fs",
                pipeline.id,
                pipeline_context.execution_id,
                pipeline_timeout,
                duration,
            )

            recorder.emit(
                PipelineFailed(
                    duration_ms=duration * 1000,
                    error=error_msg,
                    failed_step=None,
                    traceback="",
                ),
            )
            self._publish_event(
                pipeline_context,
                BusPipelineFailed(
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    duration_seconds=duration,
                    error=error_msg,
                    failed_step=None,
                ),
            )
            exc = TimeoutError(error_msg)
            exc.execution_id = pipeline_context.execution_id  # type: ignore[attr-defined]
            raise exc from None
        except asyncio.CancelledError:
            duration = time.time() - start_time
            await dag_executor.cancel()

            completed_steps = sum(
                1
                for node in nodes.values()
                if node.state in (StepState.COMPLETED, StepState.SKIPPED)
            )
            pending_steps = len(nodes) - completed_steps

            recorder.emit(
                PipelineCancelled(
                    duration_ms=duration * 1000,
                    reason="client_disconnected",
                    completed_steps=completed_steps,
                    pending_steps=pending_steps,
                ),
            )
            self._publish_event(
                pipeline_context,
                BusPipelineCancelled(
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    duration_seconds=duration,
                    reason="client_disconnected",
                    completed_steps=completed_steps,
                    pending_steps=pending_steps,
                ),
            )

            logger.info(
                "Pipeline '%s' cancelled after %.1fs "
                "(client disconnected, %d/%d steps completed)",
                pipeline.id,
                duration,
                completed_steps,
                len(nodes),
            )
            raise
        except Exception as e:
            duration = time.time() - start_time

            failed_step = None
            for node in nodes.values():
                if node.state == StepState.FAILED:
                    failed_step = node.step.name
                    break

            execution_logger.error(
                "Pipeline execution failed: pipeline=%s, "
                "execution_id=%s, duration=%.2fs, "
                "failed_step=%s, error=%s",
                pipeline.id,
                pipeline_context.execution_id,
                duration,
                failed_step,
                str(e),
            )

            import traceback as tb_mod

            recorder.emit(
                PipelineFailed(
                    duration_ms=duration * 1000,
                    error=str(e),
                    failed_step=failed_step,
                    traceback="".join(tb_mod.format_exception(e)),
                ),
            )
            self._publish_event(
                pipeline_context,
                BusPipelineFailed(
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    duration_seconds=duration,
                    error=str(e),
                    failed_step=failed_step,
                ),
            )
            e.execution_id = pipeline_context.execution_id  # type: ignore[union-attr]
            raise

        final_result = self._get_final_result(
            pipeline, pipeline_context, prepared.output_aliases
        )

        step_outputs = {
            step_id: output.text
            for step_id, output in pipeline_context.outputs.items()
            if isinstance(output, StepOutput)
        }

        backtranslation_data = self._extract_backtranslation_data(
            prepared.steps, pipeline_context
        )

        prompt_tokens = 0
        completion_tokens = 0
        reasoning_tokens = 0
        for out in pipeline_context.outputs.values():
            from .execution.map_reduce.collection import MapOutputCollection

            if isinstance(out, MapOutputCollection):
                prompt_tokens += sum(inner.prompt_tokens for inner in out.all_outputs())
                completion_tokens += sum(
                    inner.completion_tokens for inner in out.all_outputs()
                )
                reasoning_tokens += sum(
                    getattr(inner, "reasoning_tokens", 0) for inner in out.all_outputs()
                )
            elif isinstance(out, StepOutput):
                prompt_tokens += out.prompt_tokens
                completion_tokens += out.completion_tokens
                reasoning_tokens += getattr(out, "reasoning_tokens", 0)

        # Reasoning is a final-step concern, not aggregatable. Walk execution
        # order in reverse and take the first StepOutput with a non-None
        # reasoning value — mirrors how ``final_result`` selects terminal text.
        reasoning: Any = None
        for step_id in reversed(dag_executor.execution_order):
            out = pipeline_context.outputs.get(step_id)
            if isinstance(out, StepOutput) and out.reasoning is not None:
                reasoning = out.reasoning
                break

        execution_logger.info(
            f"Pipeline execution completed: pipeline={pipeline.id}, "
            f"execution_id={pipeline_context.execution_id}, "
            f"duration={duration:.2f}s, steps={len(pipeline_context.outputs)}"
        )

        hints = self._extract_output_hints(
            pipeline, pipeline_context, prepared.output_aliases
        )
        model_entity_id = extract_model_entity_id(
            pipeline_context,
            list(dag_executor.execution_order),
        )

        return PipelineExecutionOutcome(
            execution_id=pipeline_context.execution_id,
            content=final_result,
            model=pipeline.id,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                # Subset of completion_tokens; surfaced separately so consumers
                # can distinguish reasoning spend from visible output.
                "reasoning_tokens": reasoning_tokens,
            },
            duration_s=duration,
            step_outputs=step_outputs,
            backtranslation=backtranslation_data,
            execution_order=list(dag_executor.execution_order),
            reasoning=reasoning,
            model_entity_id=model_entity_id,
            hints=hints,
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
        from ..response_builder import ResponseBuilder  # noqa: I001  # late import avoids cycle

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
        """
        execution_id = self.generate_execution_id()
        prepared = self.prepare_execution(context, execution_id=execution_id)
        try:
            outcome = await self._run_prepared_execution(prepared)
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
        """Expand fragment references into full steps."""
        expanded: list[StepConfig] = []

        for item in steps:
            if isinstance(item, dict):
                if "use" in item:
                    ref = FragmentRef(**item)
                    expanded.extend(self.fragment_loader.expand_fragment_ref(ref))
                else:
                    expanded.append(StepConfig(**item))
            elif isinstance(item, FragmentRef):
                expanded.extend(self.fragment_loader.expand_fragment_ref(item))
            else:
                expanded.append(item)

        return expanded

    def _extract_output_hints(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        output_aliases: dict[str, str] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Extract structured anomaly hints from the terminal output step's JSON.

        Mirrors ``_get_final_result``'s alias-resolution logic so sub-pipeline
        output references (e.g. ``synthesize`` → ``synthesize__review_synthesis``)
        surface hints from the resolved terminal step rather than missing the
        parent-name lookup. Hints only apply to single ``StepOutput`` terminals;
        map-collection terminals and unresolved references return ``None``.
        """
        output_ref = pipeline.output
        if output_aliases and output_ref in output_aliases:
            output_ref = output_aliases[output_ref]
        output = context.get_output(output_ref)
        if not isinstance(output, StepOutput):
            return None
        if not isinstance(output.json, dict):
            return None
        hints = output.json.get("hints")
        if isinstance(hints, list):
            return hints
        return None

    def _get_final_result(
        self,
        pipeline: PipelineSpec,
        context: PipelineContext,
        output_aliases: dict[str, str] | None = None,
    ) -> str:
        """
        Get final result from pipeline output step.

        Handles:
        - Simple step references: "step_name" → StepOutput.text
        - Sub-pipeline references: "synthesize" → resolved via output_aliases
          to e.g. "synthesize__review_synthesis"
        - Map output with key: "step_name.key" → specific iteration's text
        - MapOutputCollection: concatenates all outputs with double newlines
        """
        from .execution.map_reduce.collection import MapOutputCollection

        output_ref = pipeline.output

        if output_aliases and output_ref in output_aliases:
            resolved_ref = output_aliases[output_ref]
            logger.info(
                f"Pipeline output '{output_ref}' resolved via sub-pipeline "
                f"alias to '{resolved_ref}'"
            )
            output_ref = resolved_ref
        else:
            logger.info(
                f"Pipeline output '{output_ref}' — no alias resolved "
                f"(aliases={list(output_aliases.keys()) if output_aliases else None})"
            )

        output = context.get_output(output_ref)
        if output:
            if isinstance(output, MapOutputCollection):
                if pipeline.output_format == "json_array":
                    results = []
                    for item in output.outputs_aligned():
                        if item is not None:
                            results.append(
                                item.json if item.json is not None else item.raw
                            )
                        else:
                            results.append(None)
                    return json.dumps(results)
                text_parts = [item.text for item in output.all_outputs()]
                return "\n\n".join(text_parts)
            text = output.text
            logger.info(
                f"Pipeline output '{output_ref}': text={text[:80]!r} "
                f"(raw={output.raw[:40]!r}, json={output.json is not None})"
            )
            return text

        if "." in output_ref:
            step_name, key = output_ref.split(".", 1)
            step_output = context.get_output(step_name)

            if step_output is None:
                logger.error(
                    f"Output '{output_ref}': step '{step_name}' not found "
                    f"or returned no output"
                )
                return ""

            if isinstance(step_output, MapOutputCollection):
                result = step_output.get_output_by_key(key)
                if result:
                    return result.text
                logger.warning(
                    f"Output '{output_ref}': iteration key '{key}' "
                    f"not found in map output"
                )
            else:
                logger.error(
                    f"Output '{output_ref}': step '{step_name}' is not a "
                    f"MapOutputCollection"
                )
            return ""

        available = list(context.outputs.keys())
        logger.error(
            f"Pipeline output '{output_ref}' not found in context.outputs. "
            f"Available keys: {available}"
        )
        return ""

    def _extract_backtranslation_data(
        self,
        steps: list[StepConfig],
        context: PipelineContext,
    ) -> dict[str, Any] | None:
        """Extract backtranslation data if present."""
        for step in steps:
            if step.type == "backtranslation":
                bt_output = context.get_output(step.id)
                if bt_output and bt_output.json:
                    return bt_output.json
        return None

    def _extract_messages(
        self, context: _PipelineRequestContextProtocol
    ) -> list[dict[str, Any]] | None:
        """Extract full chat messages, preferring explicit pre-truncation capture."""
        if hasattr(context.http_request, "state") and hasattr(
            context.http_request.state, "pipeline_full_messages"
        ):
            return context.http_request.state.pipeline_full_messages

        if context.original_request:
            messages = context.original_request.get("messages")
            if messages and isinstance(messages, list):
                return messages
        return None

    def _extract_source_text(self, context: _PipelineRequestContextProtocol) -> str:
        """Extract source text from request context."""
        if context.chat_request and context.chat_request.messages:
            for msg in reversed(context.chat_request.messages):
                if msg.role == "user":
                    content = msg.content
                    if isinstance(content, str):
                        return content
                    if isinstance(content, list) and content:
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                return part.get("text", "")
                            if isinstance(part, str):
                                return part

        if context.original_request:
            messages = context.original_request.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        return content

        return ""
