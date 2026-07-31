"""
Unit tests for DAG model coordination.

Tests the ModelUsageTracker and DAG executor's ability to serialize
steps that target the same model while allowing parallel execution
for steps targeting different models.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from .dag import DAGBuilder, StepNode, StepState  # noqa: E402
from .execution import DAGExecutor, ModelUsageTracker  # noqa: E402
from .handlers.protocol import StepOutput  # noqa: E402
from .schemas import StepConfig  # noqa: E402


def _build_context(registry: MagicMock) -> MagicMock:
    """Build minimal PipelineContext mock aligned with DAGExecutor contract."""
    context = MagicMock(_registry=registry)
    context.options = {}
    context.pipeline = MagicMock(
        id="test-pipeline",
        domain="test",
        source_search_path=[],
    )
    context.execution_id = "exec-test"
    context.recorder = None
    context._step_model_override = {}
    context._proxy = MagicMock()
    context._proxy.event_bus.publish_nowait = AsyncMock(return_value=None)
    return context


def _build_nodes(steps: list[StepConfig]) -> dict[str, StepNode]:
    """Build step nodes matching DAGBuilder readiness semantics."""
    return {
        step.id: StepNode(
            step=step,
            dependencies=set(),
            state=StepState.READY,
        )
        for step in steps
    }


@pytest.mark.asyncio
async def test_model_coordination_serializes_same_model():
    """Test that steps targeting the same model execute serially."""
    # Setup: 3 steps, 2 use same model
    steps = [
        StepConfig(id="step1", type="generate", model_ref="ref1", depends_on=[]),
        StepConfig(id="step2", type="generate", model_ref="ref1", depends_on=[]),
        StepConfig(id="step3", type="generate", model_ref="ref2", depends_on=[]),
    ]

    # Mock registry to return model IDs
    registry = MagicMock()
    registry.get_model_config.side_effect = lambda model_ref, **_: (
        MagicMock(model="model-a")
        if model_ref == "ref1"
        else MagicMock(model="model-b")
    )

    # Track execution order
    execution_log = []

    async def mock_execute_step(node):
        execution_log.append(f"{node.step.id}_start")
        await asyncio.sleep(0.1)  # Simulate work
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)
        execution_log.append(f"{node.step.id}_end")

    # Build DAG and execute
    nodes = _build_nodes(steps)
    context = _build_context(registry)
    executor = DAGExecutor(nodes, context)

    # Patch _execute_step to use mock
    executor._execute_step = mock_execute_step

    await executor.execute()

    # Verify: step1 and step2 didn't overlap (same model)
    step1_end_idx = execution_log.index("step1_end")
    step2_start_idx = execution_log.index("step2_start")
    assert step2_start_idx > step1_end_idx, "step2 should start after step1 ends"

    # Verify: step3 could run in parallel (different model)
    step3_start_idx = execution_log.index("step3_start")
    assert step3_start_idx < step1_end_idx
    assert step3_start_idx < step2_start_idx


@pytest.mark.asyncio
async def test_model_released_on_step_failure():
    """Fail-fast keeps dependent work from launching after model step failure."""
    steps = [
        StepConfig(id="step1", type="generate", model_ref="ref1", depends_on=[]),
        StepConfig(id="step2", type="generate", model_ref="ref1", depends_on=[]),
    ]

    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="model-a")

    execution_log = []

    async def mock_execute_step(node):
        execution_log.append(f"{node.step.id}_start")
        if node.step.id == "step1":
            raise ValueError("Simulated step1 failure")
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)
        execution_log.append(f"{node.step.id}_end")

    nodes = _build_nodes(steps)
    context = _build_context(registry)
    executor = DAGExecutor(nodes, context)
    executor._execute_step = mock_execute_step

    # Execute (step1 fails; executor is fail-fast)
    try:
        await executor.execute()
    except Exception:
        pass  # Expected (step1 failure)

    # Verify fail-fast behavior: step2 should not run after step1 failure.
    assert "step1_start" in execution_log
    assert "step2_start" not in execution_log
    assert nodes["step1"].state == StepState.FAILED


@pytest.mark.asyncio
async def test_no_model_steps_run_freely():
    """Steps without model_ref should not coordinate (run in parallel)."""
    steps = [
        StepConfig(id=f"step{i}", type="select_winner", model_ref=None, depends_on=[])
        for i in range(5)
    ]

    registry = MagicMock()
    execution_log = []

    async def mock_execute_step(node):
        execution_log.append(f"{node.step.id}_start")
        await asyncio.sleep(0.05)
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)
        execution_log.append(f"{node.step.id}_end")

    nodes = _build_nodes(steps)
    context = _build_context(registry)
    executor = DAGExecutor(nodes, context)
    executor._execute_step = mock_execute_step

    await executor.execute()

    # Verify: All steps started before any ended (parallel execution)
    first_end_idx = min(execution_log.index(f"step{i}_end") for i in range(5))
    start_count_before_first_end = sum(
        1 for log in execution_log[:first_end_idx] if "_start" in log
    )
    assert start_count_before_first_end > 1, "Multiple steps should start in parallel"


@pytest.mark.asyncio
async def test_registry_missing_model_ref():
    """If registry can't resolve model_ref, step should still launch (no blocking)."""
    steps = [
        StepConfig(id="step1", type="generate", model_ref="unknown_ref", depends_on=[]),
    ]

    # Mock registry to raise exception
    registry = MagicMock()
    registry.get_model_config.side_effect = KeyError("Model not found")

    execution_log = []

    async def mock_execute_step(node):
        execution_log.append(f"{node.step.id}_executed")
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)

    nodes = _build_nodes(steps)
    context = _build_context(registry)
    executor = DAGExecutor(nodes, context)
    executor._execute_step = mock_execute_step

    await executor.execute()

    # Verify: Step executed despite registry error
    assert "step1_executed" in execution_log


@pytest.mark.asyncio
async def test_result_completes_on_split_path_without_deadlock():
    """Split path: execute_split runs, refactor branch skips, result still completes."""
    steps = [
        StepConfig(id="plan_split", type="generate"),
        StepConfig(
            id="execute_split",
            type="generate",
            condition="plan_split.json.get('can_split') == True",
        ),
        StepConfig(
            id="plan_refactor",
            type="generate",
            condition="plan_split.json.get('can_split') == False",
        ),
        StepConfig(
            id="execute_refactor",
            type="generate",
            condition="plan_refactor.json.get('can_refactor') == True",
        ),
        StepConfig(
            id="result",
            type="select_output",
            candidates=["execute_split", "execute_refactor", "plan_split"],
        ),
    ]

    nodes = DAGBuilder(steps).build()
    context = _build_context(MagicMock())
    context.outputs = {}
    context.set_output = lambda step_id, output: context.outputs.__setitem__(
        step_id, output
    )
    context.get_output = lambda step_id: context.outputs.get(step_id)
    context.recorder = None
    context.execution_id = "exec-split"
    context.drain_step_calls = lambda _step_name: []
    context._proxy = None

    executor = DAGExecutor(nodes, context)
    executor._ensure_proxy_client = AsyncMock(return_value=MagicMock())

    async def mock_execute_step(node):
        if node.step.id == "plan_split":
            output = StepOutput(raw="split-plan", json={"can_split": True})
        elif node.step.id == "execute_split":
            output = StepOutput(raw="split-result", json={"text": "split-result"})
        elif node.step.id == "result":
            selected = context.get_output("execute_split")
            if selected is None:
                raise AssertionError("result ran before execute_split output existed")
            output = StepOutput(raw=selected.raw, json=selected.json)
        else:
            raise AssertionError(f"Unexpected executed step: {node.step.id}")

        context.set_output(node.step.id, output)
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)

    executor._execute_step = mock_execute_step
    await executor.execute()

    assert nodes["result"].state == StepState.COMPLETED
    assert context.get_output("result") is not None
    assert context.get_output("result").raw == "split-result"


@pytest.mark.asyncio
async def test_result_completes_on_refactor_path_without_deadlock():
    """Refactor path: execute_split skips, execute_refactor runs, result completes."""
    steps = [
        StepConfig(id="plan_split", type="generate"),
        StepConfig(
            id="execute_split",
            type="generate",
            condition="plan_split.json.get('can_split') == True",
        ),
        StepConfig(
            id="plan_refactor",
            type="generate",
            condition="plan_split.json.get('can_split') == False",
        ),
        StepConfig(
            id="execute_refactor",
            type="generate",
            condition="plan_refactor.json.get('can_refactor') == True",
        ),
        StepConfig(
            id="result",
            type="select_output",
            candidates=["execute_split", "execute_refactor", "plan_split"],
        ),
    ]

    nodes = DAGBuilder(steps).build()
    context = _build_context(MagicMock())
    context.outputs = {}
    context.set_output = lambda step_id, output: context.outputs.__setitem__(
        step_id, output
    )
    context.get_output = lambda step_id: context.outputs.get(step_id)
    context.recorder = None
    context.execution_id = "exec-refactor"
    context.drain_step_calls = lambda _step_name: []
    context._proxy = None

    executor = DAGExecutor(nodes, context)
    executor._ensure_proxy_client = AsyncMock(return_value=MagicMock())

    async def mock_execute_step(node):
        if node.step.id == "plan_split":
            output = StepOutput(raw="no-split", json={"can_split": False})
        elif node.step.id == "plan_refactor":
            output = StepOutput(raw="refactor-plan", json={"can_refactor": True})
        elif node.step.id == "execute_refactor":
            output = StepOutput(
                raw="refactor-result",
                json={"text": "refactor-result"},
            )
        elif node.step.id == "result":
            selected = context.get_output("execute_refactor")
            if selected is None:
                raise AssertionError(
                    "result ran before execute_refactor output existed"
                )
            output = StepOutput(raw=selected.raw, json=selected.json)
        else:
            raise AssertionError(f"Unexpected executed step: {node.step.id}")

        context.set_output(node.step.id, output)
        node.state = StepState.COMPLETED
        executor._propagate_completion(node.step.id)

    executor._execute_step = mock_execute_step
    await executor.execute()

    assert nodes["result"].state == StepState.COMPLETED
    assert context.get_output("result") is not None
    assert context.get_output("result").raw == "refactor-result"


def test_model_usage_tracker_can_acquire():
    """Test ModelUsageTracker.can_acquire logic."""
    tracker = ModelUsageTracker()

    # No model (None) should always be acquirable
    assert tracker.can_acquire(None) is True

    # Available model should be acquirable
    assert tracker.can_acquire("model-a") is True

    # Acquire model
    tracker.acquire("model-a", "step1")

    # Now it should not be acquirable
    assert tracker.can_acquire("model-a") is False

    # Different model should still be acquirable
    assert tracker.can_acquire("model-b") is True

    # Release model
    tracker.release("model-a", "step1")

    # Should be acquirable again
    assert tracker.can_acquire("model-a") is True


def test_model_usage_tracker_release_only_by_owner():
    """Test that only the owning step can release a model."""
    tracker = ModelUsageTracker()

    tracker.acquire("model-a", "step1")

    # Attempt release by different step (should be no-op)
    tracker.release("model-a", "step2")

    # Model should still be acquired
    assert tracker.can_acquire("model-a") is False

    # Release by correct step
    tracker.release("model-a", "step1")

    # Now available
    assert tracker.can_acquire("model-a") is True


@pytest.mark.asyncio
async def test_dynamic_model_ref_validation():
    """Test that dynamic model_ref raises validation error."""
    step = StepConfig(
        id="step1",
        type="generate",
        model_ref="${runtime_model}",  # Dynamic
        depends_on=[],
    )

    registry = MagicMock()

    # Should raise ValueError when trying to get target model
    with pytest.raises(ValueError, match="Dynamic model_ref not supported"):
        step.get_target_model_id(registry)


@pytest.mark.asyncio
async def test_answer_v1_runtime_model_override_drives_target_resolution():
    """Runtime `pipeline_options.model` must drive coordination for answer_v1 steps."""
    step = StepConfig(id="answer", type="generate", model_ref="answer", depends_on=[])
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="gpt-oss-20b-mxfp4-32768")

    context = MagicMock()
    context.pipeline = MagicMock(domain="answer_v1")
    context.options = {"model": "openai/gpt-5.4"}

    resolved = await step.get_target_model_id_async(
        registry,
        domain="answer_v1",
        search_path=[],
        context=context,
    )

    assert resolved == "openai/gpt-5.4"
    registry.get_model_config.assert_not_called()


def _build_answer_context(registry: MagicMock, model_override: str) -> MagicMock:
    """Build PipelineContext mock for answer_v1 with pipeline_options.model."""
    context = _build_context(registry)
    context.pipeline = MagicMock(
        id="rag-answer",
        domain="answer_v1",
        source_search_path=[],
    )
    context.options = {"model": model_override}
    context.execution_id = "exec-test"
    context.recorder = None
    context._step_model_override = {}
    context._proxy = None
    context.drain_step_calls = lambda _step_name: []
    context.outputs = {}
    context.set_output = lambda step_id, output: context.outputs.__setitem__(
        step_id, output
    )
    context.get_output = lambda step_id: context.outputs.get(step_id)
    return context


@pytest.mark.asyncio
async def test_answer_v1_runtime_override_gate_events():
    """Gate events must use the runtime override, not the static alias."""
    from .execution.dag_executor.model_coordination import StepModelCoordinator

    step = StepConfig(id="answer", type="generate", model_ref="answer", depends_on=[])
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="phi-4-q4-k-m-16384")

    context = _build_answer_context(registry, "openai/gpt-5.4")
    nodes = _build_nodes([step])
    executor = MagicMock(context=context, nodes=nodes)
    executor._observability = MagicMock()

    coordinator = StepModelCoordinator(executor)

    target_model = await coordinator.resolve_target_model(nodes["answer"])
    assert target_model == "openai/gpt-5.4"

    lock_model = coordinator.get_lock_model(nodes["answer"], target_model)
    models_in_use: set[str] = set()
    coordinator.on_step_launched(
        step_id="answer",
        target_model=target_model,
        lock_model=lock_model,
        models_in_use_this_iteration=models_in_use,
    )

    executor._observability.emit_pipeline_model_gate_claimed.assert_called_once_with(
        step_id="answer",
        model_id="openai/gpt-5.4",
    )

    coordinator.on_step_finished(
        step_id="answer",
        target_model=target_model,
        outcome="success",
    )

    executor._observability.emit_pipeline_model_gate_released.assert_called_once_with(
        step_id="answer",
        model_id="openai/gpt-5.4",
        outcome="success",
    )


@pytest.mark.asyncio
async def test_answer_v1_runtime_override_fallback_primary():
    """Fallback uses the runtime override as primary model, not the alias."""
    from .execution.dag_executor import step_model_fallback as fb_mod

    step = StepConfig(
        id="answer",
        type="generate",
        model_ref="answer",
        model_requirements={"task": "rag_answer", "source": "any"},
        depends_on=[],
    )

    context = _build_answer_context(MagicMock(), "openai/gpt-5.4")

    fallback_model = "phi-4-q4-k-m-16384"
    run_calls: list[str] = []

    async def mock_run(s: StepConfig) -> StepOutput:
        override = context._step_model_override.get(s.name)
        run_calls.append(override or "primary")
        if override == fallback_model:
            return StepOutput(raw="fallback-success", json={})
        raise TimeoutError("primary failed")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            fb_mod,
            "get_ranked_candidates",
            AsyncMock(return_value=["openai/gpt-5.4", fallback_model]),
        )

        result = await fb_mod.try_step_model_fallback(
            step,
            TimeoutError("primary failed"),
            primary_model_id="openai/gpt-5.4",
            run_step_fn=mock_run,
            context=context,
            get_event_context=lambda: ("rag-answer", "exec-test"),
            publish_event=lambda e: None,
        )

    assert result.raw == "fallback-success"
    assert fallback_model in run_calls


@pytest.mark.asyncio
async def test_answer_v1_drift_raises():
    """Drift between coordination and execution should fail fast."""
    from .dag import PipelineExecutionError
    from .execution.dag_executor.model_coordination import StepModelCoordinator

    step = StepConfig(id="answer", type="generate", model_ref="answer", depends_on=[])
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="phi-4-q4-k-m-16384")

    context = _build_answer_context(registry, "openai/gpt-5.4")
    nodes = _build_nodes([step])
    executor = MagicMock(context=context, nodes=nodes)
    executor._observability = MagicMock()

    coordinator = StepModelCoordinator(executor)

    with pytest.raises(PipelineExecutionError, match="Model resolution drift"):
        coordinator.validate_resolution_consistency(
            nodes["answer"],
            target_model="phi-4-q4-k-m-16384",
        )


@pytest.mark.asyncio
async def test_answer_v1_fallback_respects_step_model_override():
    """Answer generation should honor the active fallback override."""
    from pipelines.answer_v1.handlers.answer import AnswerGenerateHandler

    step = StepConfig(
        id="answer",
        type="generate",
        model_ref="answer",
        prompt_ref="answer_v1.answer_gated",
        depends_on=[],
    )
    context = _build_answer_context(MagicMock(), "openai/gpt-5.4")

    handler = AnswerGenerateHandler()
    calls: list[str] = []

    async def mock_override(s, ctx, model_id):
        calls.append(model_id)
        return StepOutput(raw=f"answer from {model_id}", json={})

    handler._execute_with_model_override = mock_override

    context._step_model_override["answer"] = "phi-4-q4-k-m-16384"
    result = await handler.execute(step, context)

    assert result.raw == "answer from phi-4-q4-k-m-16384"
    assert calls == ["phi-4-q4-k-m-16384"]


@pytest.mark.asyncio
async def test_local_registry_model_does_not_fallback_to_cloud_candidates():
    """Local registry-resolved primaries must not recompute cloud fallback pools."""
    from .execution.dag_executor import step_model_fallback as fb_mod
    from .execution.proxy_client import ProxyClientError

    step = StepConfig(
        id="plan",
        type="generate",
        model_ref="plan_model",
        model_requirements={"task": "code_architecture", "source": "cloud"},
        depends_on=[],
    )
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="qwen3-32b-awq-32768")
    context = _build_context(registry)
    primary_resolution = await step.get_target_model_resolution_async(
        registry,
        domain="test",
        search_path=[],
        context=context,
    )
    primary_model_id = primary_resolution.model_id if primary_resolution else None
    primary_err = ProxyClientError("Local gateway returned 503", status_code=503)
    emitted = []
    run_step = AsyncMock()
    suppressed_lookup = AsyncMock(
        side_effect=AssertionError("cloud fallback should be suppressed")
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fb_mod, "get_ranked_candidates", suppressed_lookup)
        with pytest.raises(ProxyClientError) as exc_info:
            await fb_mod.try_step_model_fallback(
                step,
                primary_err,
                primary_model_id=primary_model_id,
                primary_resolution=primary_resolution,
                run_step_fn=run_step,
                context=context,
                get_event_context=lambda: ("test-pipeline", "exec-test"),
                publish_event=emitted.append,
            )

    assert exc_info.value is primary_err
    assert primary_resolution is not None
    assert primary_resolution.came_from_registry_model_ref is True
    assert primary_resolution.is_local is True
    assert run_step.await_count == 0
    assert emitted[-1].signal == "pipeline.step.model.fallback.suppressed"
    assert emitted[-1].payload["suppression_reason"] == "routing_layer_mismatch"


@pytest.mark.asyncio
async def test_auto_cloud_step_still_falls_back_to_cloud_candidate():
    """Auto/cloud steps should preserve normal cloud-to-cloud fallback."""
    from .execution.dag_executor import step_model_fallback as fb_mod
    from .execution.proxy_client import ProxyClientError
    from .step_config import ResolvedTargetModel

    step = StepConfig(
        id="plan",
        type="generate",
        model_ref="auto",
        model_requirements={"task": "code_architecture", "source": "cloud"},
        depends_on=[],
    )
    context = _build_context(MagicMock())
    primary_resolution = ResolvedTargetModel.build(
        "openai/o3",
        resolution_source="model_requirements",
        model_ref="auto",
        requirements_source="cloud",
    )
    primary_err = ProxyClientError("Primary cloud model failed", status_code=503)

    async def run_step(step_config: StepConfig) -> StepOutput:
        override = context._step_model_override.get(step_config.name)
        if override == "openai/gpt-5.4":
            return StepOutput(raw="fallback-success", json={})
        raise AssertionError(f"unexpected fallback override {override!r}")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            fb_mod,
            "get_ranked_candidates",
            AsyncMock(return_value=["openai/o3", "openai/gpt-5.4"]),
        )
        result = await fb_mod.try_step_model_fallback(
            step,
            primary_err,
            primary_model_id=primary_resolution.model_id,
            primary_resolution=primary_resolution,
            run_step_fn=run_step,
            context=context,
            get_event_context=lambda: ("test-pipeline", "exec-test"),
            publish_event=lambda _event: None,
        )

    assert result.raw == "fallback-success"


@pytest.mark.asyncio
async def test_explicit_cloud_model_still_falls_back():
    """Registry aliases that resolve to cloud models must keep cloud fallback."""
    from .execution.dag_executor import step_model_fallback as fb_mod
    from .execution.proxy_client import ProxyClientError

    step = StepConfig(
        id="plan",
        type="generate",
        model_ref="frontier_model",
        model_requirements={"task": "code_architecture", "source": "cloud"},
        depends_on=[],
    )
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(
        model="anthropic/claude-sonnet-4"
    )
    context = _build_context(registry)
    primary_resolution = await step.get_target_model_resolution_async(
        registry,
        domain="test",
        search_path=[],
        context=context,
    )
    primary_model_id = primary_resolution.model_id if primary_resolution else None
    primary_err = ProxyClientError("Primary cloud model failed", status_code=503)

    async def run_step(step_config: StepConfig) -> StepOutput:
        override = context._step_model_override.get(step_config.name)
        if override == "openai/gpt-5.4":
            return StepOutput(raw="fallback-success", json={})
        raise AssertionError(f"unexpected fallback override {override!r}")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            fb_mod,
            "get_ranked_candidates",
            AsyncMock(return_value=["anthropic/claude-sonnet-4", "openai/gpt-5.4"]),
        )
        result = await fb_mod.try_step_model_fallback(
            step,
            primary_err,
            primary_model_id=primary_model_id,
            primary_resolution=primary_resolution,
            run_step_fn=run_step,
            context=context,
            get_event_context=lambda: ("test-pipeline", "exec-test"),
            publish_event=lambda _event: None,
        )

    assert primary_resolution is not None
    assert primary_resolution.is_cloud is True
    assert result.raw == "fallback-success"


@pytest.mark.asyncio
async def test_local_registry_model_re_raises_original_proxy_error():
    """Suppressed local->cloud fallback must preserve the original local failure."""
    from .execution.dag_executor import step_model_fallback as fb_mod
    from .execution.proxy_client import ProxyClientError

    step = StepConfig(
        id="plan",
        type="generate",
        model_ref="plan_model",
        model_requirements={"task": "code_architecture", "source": "cloud"},
        depends_on=[],
    )
    registry = MagicMock()
    registry.get_model_config.return_value = MagicMock(model="qwen3-32b-awq-32768")
    context = _build_context(registry)
    primary_resolution = await step.get_target_model_resolution_async(
        registry,
        domain="test",
        search_path=[],
        context=context,
    )
    primary_model_id = primary_resolution.model_id if primary_resolution else None
    primary_err = ProxyClientError("Local gateway returned 503", status_code=503)
    suppressed_lookup = AsyncMock(
        side_effect=AssertionError("cloud fallback should be suppressed")
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fb_mod, "get_ranked_candidates", suppressed_lookup)
        with pytest.raises(ProxyClientError) as exc_info:
            await fb_mod.try_step_model_fallback(
                step,
                primary_err,
                primary_model_id=primary_model_id,
                primary_resolution=primary_resolution,
                run_step_fn=AsyncMock(),
                context=context,
                get_event_context=lambda: ("test-pipeline", "exec-test"),
                publish_event=lambda _event: None,
            )

    assert exc_info.value is primary_err
    assert exc_info.value.status_code == 503
    assert "OpenRouter" not in str(exc_info.value)
    assert any(
        "routing layer mismatch" in note.lower()
        for note in getattr(exc_info.value, "__notes__", [])
    )
