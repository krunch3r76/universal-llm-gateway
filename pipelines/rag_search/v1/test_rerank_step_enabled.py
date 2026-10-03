"""Rerank follows the step enabled flag when options omit rerank_enabled.

Breaks when a missing options key is treated as rerank off (the pre-fix
default of false), which sends a non-empty chunk list down the passthrough
path with rerank_status disabled even though the step says enabled: true.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.core.step_config import StepConfig
from systems.pipeline.user_handlers import load_user_handlers

pytestmark = pytest.mark.offline

_LOADER = "_pipeline_handlers_rag_search_v1"


def _handler():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    HandlerRegistry._ensure_initialized()
    load_user_handlers(repo / "pipelines")
    module = importlib.import_module(f"{_LOADER}.rag_rerank_assemble")
    return module.RagRerankAssembleHandler()


@pytest.mark.asyncio
async def test_absent_rerank_enabled_key_honors_step_enabled() -> None:
    """Options without rerank_enabled and step enabled true must not passthrough."""
    handler = _handler()
    step = StepConfig.model_validate(
        {
            "name": "rerank",
            "type": "rag_rerank_assemble_v1",
            "enabled": True,
        }
    )
    context = SimpleNamespace(
        options={},
        source=SimpleNamespace(text="", messages=None),
        outputs={},
    )
    chunks = [
        {
            "content": "chunk",
            "source": "doc",
            "indexed_at": "2026-01-01",
            "metadata": {},
            "content_hash": "abc12345deadbeef",
            "score": 0.4,
        }
    ]

    def _resolve(*_args, **_kwargs):
        return chunks

    async def _cross_encoder(*_args, **_kwargs):
        return StepOutput(raw="scored", json={"rerank_status": "ok"})

    handler._resolve_input = _resolve
    handler._execute_cross_encoder_rerank = _cross_encoder
    handler._emit_rerank_event = lambda *_a, **_k: None

    out = await handler.execute(step, context)
    assert out.json["rerank_status"] != "disabled"


def _emit_context(outputs: dict):
    context = SimpleNamespace(
        options={},
        source=SimpleNamespace(text="", messages=None),
        outputs=outputs,
    )
    context.get_output = lambda name: outputs.get(name)
    return context


def _chunk() -> dict[str, str | dict | float]:
    return {
        "content": "letter body about a friend",
        "source": "familiar/emerson/sample.md",
        "indexed_at": "2026-01-01",
        "metadata": {},
        "content_hash": "abc12345deadbeef",
        "score": 0.4,
    }


@pytest.mark.asyncio
async def test_skipped_rerank_still_emits_formatted_context() -> None:
    """Break: output step is the skipped rerank, so chunks_found>0 becomes content=''."""
    module = importlib.import_module(f"{_LOADER}.rag_emit_context")
    handler = module.RagEmitContextHandler()
    step = StepConfig.model_validate(
        {
            "name": "emit_context",
            "type": "rag_emit_context_v1",
            "handler_inputs": {
                "chunks_data": "retrieve.json.chunks",
                "rerank_result": "rerank.raw",
            },
        }
    )
    context = _emit_context({"rerank": StepOutput(raw="", json={"_skipped": True})})

    def _resolve(*_args, **_kwargs):
        return [_chunk()]

    handler._resolve_input = _resolve
    out = await handler.execute(step, context)
    assert "letter body about a friend" in out.raw
    assert out.json["rerank_status"] == "disabled"


@pytest.mark.asyncio
async def test_completed_rerank_output_is_unchanged() -> None:
    """Break: emit reformats or drops the rerank step's formatted context."""
    module = importlib.import_module(f"{_LOADER}.rag_emit_context")
    handler = module.RagEmitContextHandler()
    step = StepConfig.model_validate(
        {
            "name": "emit_context",
            "type": "rag_emit_context_v1",
            "handler_inputs": {"chunks_data": "retrieve.json.chunks"},
        }
    )
    formatted = "[Source: familiar/emerson/sample.md]\n\nreranked body"
    context = _emit_context(
        {
            "rerank": StepOutput(
                raw=formatted, json={"rerank_status": "ok", "chunks_reranked": 1}
            )
        }
    )
    handler._resolve_input = lambda *_a, **_k: [_chunk()]
    out = await handler.execute(step, context)
    assert out.raw == formatted
    assert out.json["rerank_status"] == "ok"


@pytest.mark.asyncio
async def test_skipped_rerank_with_no_chunks_is_not_labeled_disabled() -> None:
    """Break: a chunks_present skip is reported as rerank disabled."""
    module = importlib.import_module(f"{_LOADER}.rag_emit_context")
    handler = module.RagEmitContextHandler()
    step = StepConfig.model_validate(
        {
            "name": "emit_context",
            "type": "rag_emit_context_v1",
            "handler_inputs": {
                "chunks_data": "retrieve.json.chunks",
                "rerank_result": "rerank.raw",
            },
        }
    )
    context = _emit_context({"rerank": StepOutput(raw="", json={"_skipped": True})})
    handler._resolve_input = lambda *_a, **_k: []
    out = await handler.execute(step, context)
    assert out.json["rerank_status"] == "skipped_no_chunks"


@pytest.mark.asyncio
async def test_dag_disabled_rerank_still_emits_pipeline_output() -> None:
    """Break: skipping rerank leaves pipeline output empty before emit_context runs."""
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    import yaml
    from systems.pipeline.core.dag import DAGBuilder, StepState
    from systems.pipeline.core.execution import DAGExecutor
    from systems.pipeline.core.executor.output_resolution import get_final_result
    from systems.pipeline.core.pipeline_config import PipelineSpec
    from systems.pipeline.core.step_controls import (
        apply_request_step_controls,
        fold_legacy_enable_flags,
    )

    repo = Path(__file__).resolve().parents[3]
    raw = yaml.safe_load((repo / "pipelines/rag_search/v1/rag-search.yaml").read_text())
    spec = PipelineSpec.model_validate(raw)
    options = fold_legacy_enable_flags({"rerank_enabled": False})
    steps = apply_request_step_controls(spec, list(spec.steps), options)
    nodes = DAGBuilder(steps).build()
    assert "rerank" in nodes["emit_context"].dependencies

    outputs: dict[str, StepOutput] = {}
    context = MagicMock()
    context.options = options
    context.pipeline = spec
    context.execution_id = "exec-emit"
    context.recorder = None
    context._step_model_override = {}
    context._proxy = MagicMock()
    context._proxy.event_bus.publish_nowait = AsyncMock(return_value=None)
    context.outputs = outputs
    context.set_output = outputs.__setitem__
    context.get_output = outputs.get

    module = importlib.import_module(f"{_LOADER}.rag_emit_context")
    emit = module.RagEmitContextHandler()
    chunk = _chunk()

    async def _execute(node):
        if node.step.id == "retrieve":
            context.set_output(
                "retrieve",
                StepOutput(raw="", json={"chunks": [chunk]}),
            )
        elif node.step.id == "emit_context":
            emit._resolve_input = lambda *_a, **_k: [chunk]
            context.set_output("emit_context", await emit.execute(node.step, context))
        else:
            context.set_output(node.step.id, StepOutput(raw="ok", json={}))
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)

    executor = DAGExecutor(nodes, context)
    executor._execute_step = _execute
    await executor.execute()

    assert nodes["rerank"].state == StepState.SKIPPED
    assert nodes["emit_context"].state == StepState.COMPLETED
    text = get_final_result(spec, context)
    assert "letter body about a friend" in text
