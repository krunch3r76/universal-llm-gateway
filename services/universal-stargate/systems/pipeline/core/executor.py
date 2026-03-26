"""
Pipeline executor with DAG-based scheduling.

Features:
- Automatic parallelization based on dependencies
- Conditional step execution
- Fragment expansion
- Domain-aware handler dispatch
- Single-writer to context.outputs (DAGExecutor only)

Invariants:
- ∀ step: dependencies complete before execution
- Parallel steps do not share mutable state
- First failure propagates immediately (fail-fast)
- Only DAGExecutor writes to context.outputs
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi.responses import Response
from universal_event_bus import Event
from universal_logging import get_logger

from ..registry import PipelineRegistry
from .dag import DAGBuilder, StepState

# New observability events (for JSONL recorder)
from .events import EventRecorder
from .events.lifecycle import (
    PipelineCancelled,
    PipelineCompleted,
    PipelineFailed,
    PipelineStarted,
)

# Old bus event factories (for backward-compatible monitoring consumers)
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
from .execution.disconnect_monitor import execute_with_disconnect_monitoring
from .execution.map_reduce.map_executor.events import ProxyProtocol
from .fragments import get_fragment_loader
from .handlers import PipelineContext, StepOutput
from .prompts import get_prompt_builder
from .schemas import FragmentRef, PipelineSpec, StepConfig


class _RequestExecutorProtocol(Protocol):
    """Minimal contract for request execution (avoids importing proxy at runtime)."""

    async def execute_request(self, context: Any) -> Any: ...


class _PipelineRequestContextProtocol(Protocol):
    """Context passed to execute(); has http_request, original_request, chat_request."""

    http_request: Any
    original_request: dict[str, Any] | None
    chat_request: Any

logger = get_logger(__name__)
# Dedicated logger for pipeline execution tracking (separate file, no truncation)
execution_logger = get_logger("systems.pipeline.execution")


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
            asyncio.create_task(event_bus.publish_async_nowait(event))
        elif not getattr(self, "_event_bus_warned", False):
            logger.warning("Event bus unavailable - events will not be published")
            self._event_bus_warned = True

    async def execute(
        self, context: _PipelineRequestContextProtocol
    ) -> Response:
        """
        Execute a pipeline using DAG-based scheduling.

        Args:
            context: Request context with pipeline model ID

        Returns:
            OpenAI-compatible chat completion response
        """
        from ..response_builder import ResponseBuilder

        pipeline = self.registry.get_pipeline(context.selected_model)

        logger.info(
            f"Executing pipeline '{pipeline.id}' "
            f"(version {pipeline.version}, type: {pipeline.type})"
        )

        # Extract source text and full conversation history
        text = self._extract_source_text(context)
        messages = self._extract_messages(context)

        # Validate original_request preservation for execution summaries
        # (Internal use only - not returned to client)
        if not context.original_request:
            logger.error(
                f"Pipeline '{pipeline.id}': original_request missing in context. "
                f"Cannot generate execution summary."
            )
            # Continue execution but execution summary will be incomplete
        elif not context.original_request.get("messages"):
            logger.warning(
                f"Pipeline '{pipeline.id}': original_request has no messages. "
                f"Execution summary will not include conversation history."
            )

        # Register inline fragments if present
        if pipeline.fragments:
            self.fragment_loader.register_inline_fragments(pipeline.fragments)

        # Expand fragments and prepare steps
        steps = self._expand_steps(pipeline.steps)

        # Build DAG (validates dependencies, detects cycles)
        dag_builder = DAGBuilder(steps)
        nodes = dag_builder.build()

        ready_count = sum(1 for n in nodes.values() if not n.dependencies)
        logger.info(
            f"Pipeline '{pipeline.id}' DAG: {len(nodes)} nodes, "
            f"{ready_count} ready for parallel execution"
        )

        # Extract runtime pipeline_options from HTTP request
        runtime_options: dict[str, Any] = {}
        if context.original_request:
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

            # Merge top-level model_ref_overrides with nested pipeline_options;
            # nested keys win on collision.
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

        # Inject corpus_hints for rag-context so suggest_terms gets vocabulary hints
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

        # Create pipeline context
        execution_id = str(uuid.uuid4())
        pipeline_context = PipelineContext(
            pipeline=pipeline,
            source_text=text,
            http_request=context.http_request,
            execution_id=execution_id,
            runtime_options=runtime_options,
            _messages=messages,
        )

        # Diagnostic: merged model_ref_overrides after YAML + runtime merge
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

        # Log pipeline execution start with full original request (no truncation)
        execution_logger.info(
            f"Pipeline execution started: pipeline={pipeline.id}, "
            f"execution_id={execution_id}, source_text='{text}'"
        )

        # Inject dependencies
        pipeline_context._registry = self.registry
        pipeline_context._request_executor = self.request_executor
        pipeline_context._proxy = self.proxy

        # Emit sub-pipeline expansion events so expansion stays observable on bus.
        if dag_builder.output_aliases:
            for (
                parent_step_name,
                resolved_output_step,
            ) in dag_builder.output_aliases.items():
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

        # Create event recorder for pipeline observability
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

        # Pass gateway information for eviction protection
        if (
            hasattr(context, "selected_gateway_instance")
            and context.selected_gateway_instance
        ):
            gateway_name = context.selected_gateway_instance.config.name
            pipeline_context.selected_gateway_instance = gateway_name

        # Emit pipeline started event (recorder for JSONL + bus for monitoring)
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

        start_time = time.time()
        pipeline_timeout = float(
            pipeline_context.options.get("timeout_seconds", 60)
        )

        try:
            await asyncio.wait_for(
                execute_with_disconnect_monitoring(
                    dag_executor=dag_executor,
                    http_request=pipeline_context.http_request,
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    step_count=len(nodes),
                ),
                timeout=pipeline_timeout,
            )
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

            error_msg = (
                f"Pipeline '{pipeline.id}' timed out after {pipeline_timeout}s"
            )
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

        # Extract final result, resolving sub-pipeline output aliases
        final_result = self._get_final_result(
            pipeline, pipeline_context, dag_builder.output_aliases
        )

        # Convert outputs for response builder (skip MapOutputCollection)
        step_outputs = {
            step_id: output.text
            for step_id, output in pipeline_context.outputs.items()
            if isinstance(output, StepOutput)
        }

        # Check for backtranslation data
        backtranslation_data = self._extract_backtranslation_data(
            steps, pipeline_context
        )

        response = ResponseBuilder.build_response(
            pipeline_context,
            final_result,
            pipeline,
            step_outputs,
            backtranslation_data,
            execution_order=dag_executor.execution_order,
        )

        # Log successful completion
        duration = time.time() - start_time
        execution_logger.info(
            f"Pipeline execution completed: pipeline={pipeline.id}, "
            f"execution_id={pipeline_context.execution_id}, "
            f"duration={duration:.2f}s, steps={len(pipeline_context.outputs)}"
        )

        try:
            return response
        finally:
            # Guaranteed flush even if response serialisation raises
            recorder.close()

    def _expand_steps(
        self,
        steps: Sequence[StepConfig | FragmentRef | dict[str, Any]],
    ) -> list[StepConfig]:
        """Expand fragment references into full steps."""
        expanded: list[StepConfig] = []

        for item in steps:
            # Handle dict (raw from YAML)
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

        # Resolve sub-pipeline aliases: "synthesize" → "synthesize__review_synthesis"
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

        # Try direct lookup first (for simple step names)
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

        # Handle dotted references like "synthesize_all.qwen"
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

        # Fallback to original_request (from raw body bytes — pre-truncation)
        if context.original_request:
            messages = context.original_request.get("messages")
            if messages and isinstance(messages, list):
                return messages
        return None

    def _extract_source_text(
        self, context: _PipelineRequestContextProtocol
    ) -> str:
        """Extract source text from request context."""
        # From chat request
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

        # From original request
        if context.original_request:
            messages = context.original_request.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        return content

        return ""
