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

import json
import time
from dataclasses import fields, replace
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
from ..execution.checkpoint import CheckpointManager, FilesystemCheckpointBackend
from ..handlers import PipelineContext
from ..schemas import (  # PipelineSpec: checkpoint wiring
    FragmentRef,
    PipelineSpec,
    StepConfig,
)
from ..step_types import CheckpointConfig
from .input_extraction import (
    extract_chat_id,
    extract_dispatch_thread_id,
    extract_messages,
    extract_source_text,
)
from .prepared import (
    PreparedPipelineExecution,
    _PipelineRequestContextProtocol,
    execution_logger,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .pipeline_executor import PipelineExecutor

logger = get_logger(__name__)

_CHECKPOINT_CONFIG_FIELDS = {f.name for f in fields(CheckpointConfig)}


def _stripped_str(raw: Any) -> str | None:
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped:
            return stripped
    return None


def extract_resume_of(context: _PipelineRequestContextProtocol) -> str | None:
    """Return ``resume_of`` from ``pipeline_options``, else the top-level request."""
    if not context.original_request:
        return None
    options = context.original_request.get("pipeline_options")
    if isinstance(options, dict):
        found = _stripped_str(options.get("resume_of"))
        if found:
            return found
    return _stripped_str(context.original_request.get("resume_of"))


def extract_continuation_input(context: _PipelineRequestContextProtocol) -> Any:
    """Return ``continuation_input`` from options or the top-level request."""
    if not context.original_request:
        return None
    options = context.original_request.get("pipeline_options")
    if isinstance(options, dict) and "continuation_input" in options:
        return options.get("continuation_input")
    if "continuation_input" in context.original_request:
        return context.original_request.get("continuation_input")
    return None


def resolve_checkpoint_config(
    checkpoint_raw: dict[str, Any] | None,
    *,
    resume_of: str | None,
    stops_declared: bool = False,
) -> CheckpointConfig | None:
    """Build checkpoint config when a memo is required.

    A memo is required when YAML enables checkpoints, the request carries
    ``resume_of``, or the pipeline declares ``stops:``.
    """
    memo_required = bool(resume_of) or stops_declared
    if not checkpoint_raw and not memo_required:
        return None

    if checkpoint_raw:
        kwargs = {
            k: v for k, v in checkpoint_raw.items() if k in _CHECKPOINT_CONFIG_FIELDS
        }
        config = CheckpointConfig(**kwargs)
    else:
        config = CheckpointConfig()

    if not config.enabled and not memo_required:
        return None
    if memo_required and not config.enabled:
        config = replace(config, enabled=True)
    return config


def _lineage_root_for_run(
    *,
    resume_of: str | None,
    execution_id: str,
) -> tuple[str, int]:
    """Root id and continuation sequence. A root run is ``(execution_id, 0)``."""
    if not resume_of:
        return execution_id, 0
    from ..execution.dispatch_journal import _journal_path
    from ..execution.dispatch_journal_transitions import resolve_lineage_root_sync

    return resolve_lineage_root_sync(_journal_path(), resume_of)


def build_checkpoint_manager_if_needed(
    executor: PipelineExecutor,
    pipeline: PipelineSpec,
    context: _PipelineRequestContextProtocol,
    execution_id: str,
) -> CheckpointManager | None:
    """Construct a lineage-scoped checkpoint manager or return ``None``."""
    resume_of = extract_resume_of(context)
    stops_declared = pipeline.stops is not None
    config = resolve_checkpoint_config(
        pipeline.checkpoint,
        resume_of=resume_of,
        stops_declared=stops_declared,
    )
    if config is None:
        return None

    lineage_root, _seq = _lineage_root_for_run(
        resume_of=resume_of,
        execution_id=execution_id,
    )
    backend = FilesystemCheckpointBackend(config.storage_path)
    event_bus = None
    proxy = getattr(executor, "proxy", None)
    if proxy is not None:
        event_bus = getattr(proxy, "event_bus", None)

    return CheckpointManager(
        backend,
        config,
        pipeline.id,
        execution_id=lineage_root,
        event_bus=event_bus,
    )


def _lineage_envelope(context: _PipelineRequestContextProtocol) -> str:
    request = context.original_request or {}
    options = request.get("pipeline_options")
    messages = request.get("messages")
    envelope = {
        "_lineage_request": True,
        "pipeline_options": options if isinstance(options, dict) else {},
        "messages": messages if isinstance(messages, list) else None,
    }
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=True,
    )


def _merge_lineage_request(
    context: _PipelineRequestContextProtocol,
    *,
    stored: dict[str, Any],
    messages: list[dict[str, Any]] | None,
    source_text: str,
    resume_of: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Root options first, continuation options on top. Messages come from the root."""
    request = context.original_request
    if not isinstance(request, dict):
        request = {}
    context.original_request = request
    current = request.get("pipeline_options")
    current_options = current if isinstance(current, dict) else {}
    if stored.get("_lineage_request") is True:
        inherited = stored.get("pipeline_options")
        stored_messages = stored.get("messages")
    else:
        inherited = stored
        stored_messages = None
    inherited_options = inherited if isinstance(inherited, dict) else {}
    explicit_input = extract_continuation_input(context)
    merged = {**inherited_options, **current_options}
    if explicit_input is not None:
        merged["continuation_input"] = explicit_input
    elif messages:
        merged["continuation_input"] = messages
    if "resume_of" not in merged:
        merged["resume_of"] = resume_of
    request["pipeline_options"] = merged
    if isinstance(stored_messages, list):
        messages = stored_messages
    else:
        messages = None
    request["messages"] = messages
    return source_text, messages


def _apply_lineage(
    pipeline: PipelineSpec,
    context: _PipelineRequestContextProtocol,
    execution_id: str,
    *,
    text: str,
    messages: list[dict[str, Any]] | None,
    journal_backed: bool,
) -> tuple[str, list[dict[str, Any]] | None, str, int]:
    """Inherit the root request on continue, and pin a journaled root run."""
    from ..execution.dispatch_journal import _journal_path, pipeline_steps_sha256
    from ..execution.dispatch_journal_transitions import (
        read_lineage_sync,
        write_lineage_root_sync,
    )

    resume_of = extract_resume_of(context)
    lineage_root, continuation_seq = _lineage_root_for_run(
        resume_of=resume_of,
        execution_id=execution_id,
    )
    if not resume_of:
        if journal_backed:
            write_lineage_root_sync(
                _journal_path(),
                root_id=execution_id,
                pipeline_id=pipeline.id,
                version=str(pipeline.version),
                steps_sha256=pipeline_steps_sha256(pipeline),
                source_text=text,
                options_json=_lineage_envelope(context),
            )
        return text, messages, lineage_root, continuation_seq

    lineage = read_lineage_sync(_journal_path(), lineage_root)
    if lineage is None:
        return text, messages, lineage_root, continuation_seq
    try:
        stored = json.loads(lineage["options_json"])
    except json.JSONDecodeError:
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    text, messages = _merge_lineage_request(
        context,
        stored=stored,
        messages=messages,
        source_text=str(lineage["source_text"]),
        resume_of=resume_of,
    )
    return text, messages, lineage_root, continuation_seq


def do_prepare_execution(
    executor: PipelineExecutor,
    context: _PipelineRequestContextProtocol,
    *,
    execution_id: str,
    journal_backed: bool = False,
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

    resume_of = extract_resume_of(context)
    if resume_of:
        from ..execution.dispatch_journal import (
            _claimed_at_now,
            _journal_path,
            pipeline_steps_sha256,
        )
        from ..execution.dispatch_journal_transitions import (
            ContinuationRefusedError,
            assess_continuation_sync,
        )

        decision = assess_continuation_sync(
            _journal_path(),
            stop_execution_id=resume_of,
            successor_execution_id=execution_id,
            steps_sha256=pipeline_steps_sha256(pipeline),
            pipeline_id=pipeline.id,
            claimed_at=_claimed_at_now(),
        )
        if not decision.admitted:
            raise ContinuationRefusedError(decision)
        if decision.fresh_claim:
            context._continuation_fresh_claim = (resume_of, execution_id)

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

    text, messages, lineage_root, continuation_seq = _apply_lineage(
        pipeline,
        context,
        execution_id,
        text=text,
        messages=messages,
        journal_backed=journal_backed,
    )
    runtime_options = extract_runtime_options(context, pipeline)

    steps = expand_steps(executor, pipeline.steps)
    from ..step_controls import apply_request_step_controls

    steps = apply_request_step_controls(pipeline, steps, runtime_options)

    dag_builder = DAGBuilder(steps)
    nodes = dag_builder.build()
    output_aliases = dict(dag_builder.output_aliases or {})

    ready_count = sum(1 for n in nodes.values() if not n.dependencies)
    logger.info(
        f"Pipeline '{pipeline.id}' DAG: {len(nodes)} nodes, "
        f"{ready_count} ready for parallel execution"
    )

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
        lineage_root=lineage_root,
        continuation_seq=continuation_seq,
        runtime_options=runtime_options,
        _messages=messages,
        chat_id=extract_chat_id(context),
        dispatch_thread_id=extract_dispatch_thread_id(context),
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

    checkpoint_manager = build_checkpoint_manager_if_needed(
        executor,
        pipeline,
        context,
        execution_id,
    )
    dag_executor = DAGExecutor(
        nodes,
        pipeline_context,
        checkpoint_manager=checkpoint_manager,
    )

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
    """Flatten ``pipeline_options`` + merged ``model_ref_overrides`` from request.

    Copies ``original_request["pipeline_options"]`` (raising ``ValueError`` if it is
    not a dict), merges top-level and nested ``model_ref_overrides`` (nested wins),
    surfaces the outer ``stream`` flag, and sets ``chat_completions_only`` from the
    caller ``model`` via frontier_consult admission. Returns ``{}`` with no request.
    """  # noqa: E501
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

    # Surface the outer ``stream`` flag (coerced at proxy ingress) for the
    # generate handler's streaming branch.
    if "stream" in context.original_request:
        runtime_options["stream"] = context.original_request["stream"]

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

    # Single source for chat-completions-only branch conditions (chat-dispatch
    # respond vs respond_cc). Import the admission predicate — do not re-derive
    # the -search-api suffix rule in YAML or a second helper.
    caller_model = runtime_options.get("model")
    if isinstance(caller_model, str):
        caller_model = caller_model.strip()
    else:
        caller_model = ""
    if caller_model:
        from systems.frontier_consult.admission import is_chat_completions_only

        runtime_options["chat_completions_only"] = is_chat_completions_only(
            caller_model
        )
    else:
        runtime_options["chat_completions_only"] = False

    return runtime_options


def expand_steps(
    executor: PipelineExecutor,
    steps: Sequence[StepConfig | FragmentRef | dict[str, Any]],
) -> list[StepConfig]:
    """Flatten a pipeline step list by expanding fragment references into StepConfigs.

    Dict items with a ``use`` key become FragmentRef and are expanded through
    ``executor.fragment_loader``; other dicts are parsed as StepConfig; existing
    FragmentRef/StepConfig objects are expanded or passed through, preserving order.
    """
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
