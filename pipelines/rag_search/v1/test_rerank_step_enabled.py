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
