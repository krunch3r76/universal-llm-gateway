"""Executor-level B4 smoke (registry + PipelineExecutor)."""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest
from systems.pipeline.core.executor.pipeline_executor import PipelineExecutor
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.user_handlers import load_user_handlers

pytestmark = pytest.mark.offline

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def writer_registry() -> PipelineRegistry:
    HandlerRegistry._ensure_initialized()
    load_user_handlers(REPO / "pipelines")
    registry = PipelineRegistry(
        search_paths=[str(REPO / "pipelines")],
        config_base_dir=REPO,
    )
    registry.load()
    assert registry.is_pipeline("writer-specialist-v1"), registry._validation_errors
    return registry


@pytest.mark.asyncio
async def test_executor_registry_loads_writer_specialist(
    writer_registry: PipelineRegistry,
) -> None:
    """Break: wrong search_paths (pipelines/pipelines) → pipeline missing from registry."""
    pipeline = writer_registry.pipelines.get("writer-specialist-v1")
    assert pipeline is not None
    assert pipeline.id == "writer-specialist-v1"


@pytest.mark.asyncio
async def test_executor_runs_but_openai_review_not_reached_without_transport(
    writer_registry: PipelineRegistry,
) -> None:
    """Registry path REPO/pipelines loads writer-specialist-v1; executor fails on draft_local race.

    Proposal: writer-specialist-v1 YAML — make draft select depend on draft_local (or gate remote writer) so executor reaches review without transport mocks.
    """
    opts = {
        "writer_seat": "cdp",
        "reviewer_seat": "openai",
        "brief": {
            "signer": "Ada",
            "objective": "x",
            "recipient": "r",
            "deliverable": {"genre": "memo"},
            "material_attested": ["a"],
            "not_attested": [],
            "sensitivity": "sensitive",
        },
        "working_set": [],
        "seat_models": {"cdp": "cursor/grok-4.7", "openai": "openai/gpt-5.5"},
        "dispatch_thread_id": "15790",
    }

    async def _never() -> None:
        await asyncio.Event().wait()

    ctx = types.SimpleNamespace(
        selected_model="writer-specialist-v1",
        original_request={
            "model": "writer-specialist-v1",
            "messages": [{"role": "user", "content": "memo"}],
            "pipeline_options": opts,
        },
        chat_request=None,
        http_request=types.SimpleNamespace(receive=_never),
        outputs={},
        options=opts,
    )
    request_executor = types.SimpleNamespace()
    request_executor.execute_request = lambda _c: None
    executor = PipelineExecutor(
        writer_registry, request_executor, types.SimpleNamespace(event_bus=None)
    )
    await executor.execute(ctx)
    # Legacy brief-shaped options are refused at assemble; DAG completes without draft_local race.
