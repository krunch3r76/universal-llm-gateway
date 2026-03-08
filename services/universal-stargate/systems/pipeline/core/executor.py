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
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi.responses import Response
from universal_event_bus import Event
from universal_logging import get_logger

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
from .fragments import get_fragment_loader
from .handlers import PipelineContext, StepOutput
from .prompts import get_prompt_builder
from .schemas import FragmentRef, StepConfig

if TYPE_CHECKING:
    from ..registry import PipelineRegistry
    from .schemas import PipelineSpec

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
        request_executor: Any,
        proxy: Any,
    ):
        self.registry = registry
        self.request_executor = request_executor
        self.proxy = proxy
        self.prompt_builder = get_prompt_builder()
        self.fragment_loader = get_fragment_loader()
        # Warn once if event bus unavailable
        self._event_bus_warned: bool = False

    def _publish_event(self, context: PipelineContext, event: Event) -> None:
        """
        Publish event via context's event bus (fire-and-forget).

        Reuses pattern from DAGExecutor for consistency.
        Logs WARN once if event_bus unavailable (graceful degradation).
        """
        proxy = getattr(context, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            asyncio.create_task(event_bus.publish_async_nowait(event))
        elif not getattr(self, "_event_bus_warned", False):
            logger.warning("Event bus unavailable - events will not be published")
            self._event_bus_warned = True

    async def execute(self, context) -> Response:
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
            raw_options = context.original_request.get("pipeline_options", {})
            if raw_options is not None and not isinstance(raw_options, dict):
                raise ValueError(
                    f"Invalid pipeline_options type: expected dict, "
                    f"got {type(raw_options).__name__}"
                )
            runtime_options = raw_options if raw_options else {}
            if runtime_options:
                option_keys = list(runtime_options.keys())
                logger.info(
                    f"Pipeline '{pipeline.id}': Received runtime options: {option_keys}"
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

        # Execute DAG with client disconnection monitoring
        import time

        dag_executor = DAGExecutor(nodes, pipeline_context)
        start_time = time.time()

        try:
            # Race between DAG execution and client disconnection
            await execute_with_disconnect_monitoring(
                dag_executor=dag_executor,
                http_request=pipeline_context.http_request,
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                step_count=len(nodes),
            )
            duration = time.time() - start_time

            # Emit pipeline completed event (recorder + bus)
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
        except asyncio.CancelledError:
            # Client disconnected - clean up and emit cancellation event
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
                f"Pipeline '{pipeline.id}' cancelled after {duration:.1f}s "
                f"(client disconnected, {completed_steps}/{len(nodes)} steps completed)"
            )
            raise
        except Exception as e:
            duration = time.time() - start_time

            # Determine which step failed (if any)
            failed_step = None
            for node in nodes.values():
                if node.state == StepState.FAILED:
                    failed_step = node.step.name
                    break

            # Log failure with execution_id and full error (no truncation)
            execution_logger.error(
                f"Pipeline execution failed: pipeline={pipeline.id}, "
                f"execution_id={pipeline_context.execution_id}, "
                f"duration={duration:.2f}s, failed_step={failed_step}, "
                f"error={str(e)}"
            )

            # Emit pipeline failed event (recorder + bus)
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
            # Attach execution_id so HTTP error response can include it
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
            context,
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
                    # Fragment reference
                    ref = FragmentRef(**item)
                    fragment_steps = self.fragment_loader.expand_fragment_ref(ref)
                    expanded.extend(fragment_steps)
                else:
                    # Regular step
                    expanded.append(StepConfig(**item))
            elif isinstance(item, FragmentRef):
                fragment_steps = self.fragment_loader.expand_fragment_ref(item)
                expanded.extend(fragment_steps)
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
                        if item is not None and item.json is not None:
                            results.append(item.json)
                        elif item is not None:
                            results.append(item.raw)
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
        else:
            available = list(context.outputs.keys())
            logger.error(
                f"Pipeline output '{output_ref}' not found in context.outputs. "
                f"Available keys: {available}"
            )

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
                # Try key-based access
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

    def _extract_messages(self, context: Any) -> list[dict[str, Any]] | None:
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

    def _extract_source_text(self, context: Any) -> str:
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
