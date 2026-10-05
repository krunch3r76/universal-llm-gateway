"""Generic (non-rag-search) request-time step controls."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from .dag import StepNode, StepState  # noqa: E402
from .execution import DAGExecutor  # noqa: E402
from .schemas import PipelineSpec, StepConfig  # noqa: E402
from .step_controls import (  # noqa: E402
    StepCallerError,
    apply_request_step_controls,
)


def _orchestration_pipeline(
    *,
    a_enabled: bool = True,
    include_sub: bool = True,
    include_c: bool = False,
) -> PipelineSpec:
    steps: list[StepConfig] = [
        StepConfig.model_validate(
            {
                "name": "a",
                "type": "generate",
                "enabled": a_enabled,
                "allow_disable": True,
            }
        ),
        StepConfig.model_validate(
            {
                "name": "b",
                "type": "generate",
                "allow_disable": False,
            }
        ),
    ]
    if include_sub:
        steps.append(
            StepConfig.model_validate(
                {
                    "name": "sub",
                    "type": "sub_pipeline",
                    "allow_disable": True,
                }
            )
        )
    if include_c:
        steps.append(
            StepConfig.model_validate(
                {
                    "name": "c",
                    "type": "generate",
                    "allow_disable": True,
                }
            )
        )
    return PipelineSpec.model_validate(
        {
            "id": "orchestration-test",
            "version": "1.0",
            "type": "test",
            "category": "test",
            "output": "a",
            "steps": steps,
        }
    )


def _rag_search_pipeline() -> PipelineSpec:
    return PipelineSpec.model_validate(
        {
            "id": "rag-search",
            "version": "1.0",
            "type": "rag_search",
            "category": "rag_search",
            "output": "generate_hyde",
            "steps": [
                StepConfig.model_validate(
                    {
                        "name": "generate_hyde",
                        "type": "generate",
                        "allow_disable": True,
                    }
                ),
            ],
        }
    )


def test_non_rag_skip_steps_disables_step() -> None:
    pipeline = _orchestration_pipeline()
    updated = apply_request_step_controls(
        pipeline,
        pipeline.steps,
        {"skip_steps": ["a"]},
    )
    by_id = {s.id: s for s in updated}
    assert by_id["a"].get_domain_field("enabled", True) is False


def test_non_rag_step_overrides_enable() -> None:
    pipeline = _orchestration_pipeline(a_enabled=False)
    updated = apply_request_step_controls(
        pipeline,
        pipeline.steps,
        {"step_overrides": {"a": {"enabled": True}}},
    )
    by_id = {s.id: s for s in updated}
    assert by_id["a"].get_domain_field("enabled", True) is True


def test_non_rag_disable_refused_without_allow_disable() -> None:
    pipeline = _orchestration_pipeline()
    with pytest.raises(StepCallerError, match="allow_disable"):
        apply_request_step_controls(
            pipeline,
            pipeline.steps,
            {"skip_steps": ["b"]},
        )


def test_non_rag_unknown_step_is_caller_error() -> None:
    pipeline = _orchestration_pipeline()
    with pytest.raises(StepCallerError, match="unknown step"):
        apply_request_step_controls(
            pipeline,
            pipeline.steps,
            {"step_overrides": {"missing": {"enabled": False}}},
        )


def test_non_rag_enable_and_skip_conflict() -> None:
    pipeline = _orchestration_pipeline()
    with pytest.raises(StepCallerError):
        apply_request_step_controls(
            pipeline,
            pipeline.steps,
            {
                "step_overrides": {"a": {"enabled": True}},
                "skip_steps": ["a"],
            },
        )


def test_banned_legacy_flags_only_rejected_for_rag_search() -> None:
    orch = _orchestration_pipeline()
    apply_request_step_controls(orch, orch.steps, {"hyde_enabled": True})

    rag = _rag_search_pipeline()
    with pytest.raises(StepCallerError):
        apply_request_step_controls(rag, rag.steps, {"hyde_enabled": True})


def test_sub_pipeline_toggle_refused() -> None:
    pipeline = _orchestration_pipeline()
    with pytest.raises(StepCallerError, match="sub_pipeline"):
        apply_request_step_controls(
            pipeline,
            pipeline.steps,
            {"skip_steps": ["sub"]},
        )


def _build_context(registry: MagicMock) -> MagicMock:
    context = MagicMock(_registry=registry)
    context.options = {}
    context.pipeline = MagicMock(
        id="orchestration-test",
        domain="test",
        source_search_path=[],
    )
    context.execution_id = "exec-test"
    context.recorder = None
    context._step_model_override = {}
    context._proxy = MagicMock()
    context._proxy.event_bus.publish_nowait = AsyncMock(return_value=None)
    context.set_output = MagicMock()
    return context


def _build_nodes(steps: list[StepConfig]) -> dict[str, StepNode]:
    return {
        step.id: StepNode(
            step=step,
            dependencies=set(),
            state=StepState.READY,
        )
        for step in steps
    }


@pytest.mark.asyncio
async def test_skipped_step_never_launches() -> None:
    pipeline = _orchestration_pipeline(include_sub=False, include_c=True)
    controlled = apply_request_step_controls(
        pipeline,
        [s for s in pipeline.steps if s.id in ("a", "c")],
        {"skip_steps": ["a"]},
    )
    steps = [s for s in controlled if s.id in ("a", "c")]
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="model-a")

    launched: list[str] = []

    async def mock_execute_step(node):
        launched.append(node.step.id)
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)

    nodes = _build_nodes(steps)
    context = _build_context(registry)
    executor = DAGExecutor(nodes, context)
    executor._execute_step = mock_execute_step

    await executor.execute()

    assert "a" not in launched
    assert nodes["a"].state == StepState.SKIPPED
    assert "c" in launched
