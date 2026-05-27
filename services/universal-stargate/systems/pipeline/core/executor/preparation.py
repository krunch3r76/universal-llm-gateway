"""Pipeline preparation — resolve spec, build DAG, emit start events.

Performs all synchronous setup prior to DAG execution: registry lookup,
fragment expansion, DAG construction, context creation, dependency
injection, and initial event emission. Emits ``PipelineStarted`` on
both the JSONL recorder and the event bus.

Invariants:
- ``generate_execution_id()`` is called exactly once per dispatch
  (by ``PipelineExecutor.generate_execution_id``); the minted id is
  threaded through ``do_prepare_execution`` so sync + async paths
  share identity with the DAG.
- ``pipeline_context._registry``, ``_request_executor``, ``_proxy``,
  and ``_recorder`` are populated here before the DAG runs.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ..dag import DAGBuilder
from ..events import EventRecorder
from ..events.lifecycle import PipelineStarted
from ..events.pipeline import (
    PipelineStarted as BusPipelineStarted,
)
from ..events.step import (
    SubPipelineExpanded as BusSubPipelineExpanded,
)
from ..execution import DAGExecutor
from ..handlers import PipelineContext
from ..schemas import FragmentRef, PipelineSpec, StepConfig
from .input_extraction import extract_chat_id, extract_messages, extract_source_text
from .prepared import (
    PreparedPipelineExecution,
    _PipelineRequestContextProtocol,
    execution_logger,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .pipeline_executor import PipelineExecutor

logger = get_logger(__name__)


def do_prepare_execution(
    executor: PipelineExecutor,
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
    pipeline = executor.registry.get_pipeline(context.selected_model)

    logger.info(
        f"Executing pipeline '{pipeline.id}' "
        f"(version {pipeline.version}, type: {pipeline.type})"
    )

    text = extract_source_text(context)
    messages = extract_messages(context)

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
        executor.fragment_loader.register_inline_fragments(pipeline.fragments)

    steps = expand_steps(executor, pipeline.steps)

    dag_builder = DAGBuilder(steps)
    nodes = dag_builder.build()
    output_aliases = dict(dag_builder.output_aliases or {})

    ready_count = sum(1 for n in nodes.values() if not n.dependencies)
    logger.info(
        f"Pipeline '{pipeline.id}' DAG: {len(nodes)} nodes, "
        f"{ready_count} ready for parallel execution"
    )

    runtime_options = extract_runtime_options(context, pipeline)

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
        chat_id=extract_chat_id(context),
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

    pipeline_context._registry = executor.registry
    pipeline_context._request_executor = executor.request_executor
    pipeline_context._proxy = executor.proxy

    if output_aliases:
        for (
            parent_step_name,
            resolved_output_step,
        ) in output_aliases.items():
            prefix = f"{parent_step_name}__"
            expanded_count = sum(
                1 for node_step_name in nodes if node_step_name.startswith(prefix)
            )
            executor._publish_event(
                pipeline_context,
                BusSubPipelineExpanded(
                    pipeline_id=pipeline.id,
                    execution_id=pipeline_context.execution_id,
                    parent_step_name=parent_step_name,
                    resolved_output_step=resolved_output_step,
                    expanded_step_count=expanded_count,
                ),
            )

    log_base = pipeline_context.options.get("log_dir", "/tmp/logs/universal-stargate")
    exec_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    exec_short = execution_id[:8]
    event_dir = (
        Path(log_base) / "pipeline_summaries" / pipeline.id / f"{exec_ts}_{exec_short}"
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
    executor._publish_event(
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


def extract_runtime_options(
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
            f"Invalid pipeline_options type: expected dict, got {type(raw_po).__name__}"
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

    # Surface the outer ``stream`` flag so the generate handler's streaming
    # branch (terminal-passthrough eligible pipelines, see Phase 3 of
    # plan:pipeline-terminal-passthrough-streaming) can detect it via
    # ``context.runtime_options.get("stream")``. Coerced to plain bool so
    # downstream code does not need to handle truthy/falsy strings.
    if "stream" in context.original_request:
        runtime_options["stream"] = bool(context.original_request.get("stream"))

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


def expand_steps(
    executor: PipelineExecutor,
    steps: Sequence[StepConfig | FragmentRef | dict[str, Any]],
) -> list[StepConfig]:
    """Expand fragment references into full steps."""
    expanded: list[StepConfig] = []

    for item in steps:
        if isinstance(item, dict):
            if "use" in item:
                ref = FragmentRef(**item)
                expanded.extend(executor.fragment_loader.expand_fragment_ref(ref))
            else:
                expanded.append(StepConfig(**item))
        elif isinstance(item, FragmentRef):
            expanded.extend(executor.fragment_loader.expand_fragment_ref(item))
        else:
            expanded.append(item)

    return expanded
