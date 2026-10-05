"""Lineage-scoped step memo: reload completed steps, never memo stop outputs."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from ..dag import StepNode, StepState  # noqa: E402
from ..executor.preparation import build_checkpoint_manager_if_needed  # noqa: E402
from ..handlers.step_output import StepOutput, stop  # noqa: E402
from ..pipeline_config import PipelineSpec  # noqa: E402
from ..schemas import StepConfig  # noqa: E402
from ..step_types import CheckpointConfig  # noqa: E402
from . import DAGExecutor  # noqa: E402
from .checkpoint import CheckpointManager, FilesystemCheckpointBackend  # noqa: E402
from .step_wrapper import execute_step_with_wrappers  # noqa: E402


def _step(step_id: str) -> StepConfig:
    return StepConfig(id=step_id, type="select_winner", checkpoint=True)


def _nodes(spec: list[tuple[str, set[str]]]) -> dict[str, StepNode]:
    nodes = {
        step_id: StepNode(
            step=_step(step_id),
            dependencies=set(deps),
            state=StepState.READY if not deps else StepState.PENDING,
        )
        for step_id, deps in spec
    }
    for step_id, deps in spec:
        for dep in deps:
            nodes[dep].dependents.add(step_id)
    return nodes


def _context(*, pipeline_id: str = "lineage-memo-pipe") -> MagicMock:
    context = MagicMock()
    context.outputs = {}
    context.options = {}
    context.execution_id = "exec-new"
    context.recorder = None
    context._step_model_override = {}
    context._step_progress_by_step = {}
    context._registry = MagicMock()
    context.pipeline = MagicMock()
    context.pipeline.id = pipeline_id
    context.pipeline.stops = None
    context.drain_step_calls = lambda _name: []
    context.set_output = lambda step_id, output: context.outputs.__setitem__(
        step_id, output
    )
    context.get_output = lambda step_id: context.outputs.get(step_id)
    context._proxy = MagicMock()
    context._proxy.event_bus.publish_nowait = AsyncMock(return_value=None)
    return context


class _RecordingEventBus:
    def __init__(self) -> None:
        self.loaded_steps: list[str] = []

    async def publish_nowait(self, event) -> None:  # type: ignore[no-untyped-def]
        if getattr(event, "signal", None) == "pipeline.checkpoint.loaded":
            self.loaded_steps.append(event.payload["step_name"])


@pytest.mark.asyncio
async def test_lineage_memo_reloads_s1_reruns_s2_on_stop(
    tmp_path: Path,
) -> None:
    """Run 2 loads s1 from disk and re-executes the stopping step s2.

    Breaks when stop outputs are memoized (permanent stop) or lineage keys
    differ (s1 side effects repeat).
    """
    ckpt_dir = tmp_path / "pipeline_checkpoints"
    pipeline_id = "lineage-memo-pipe"
    lineage_root = "lineage-root-1"
    config = CheckpointConfig(
        enabled=True,
        strategy="per_step",
        storage_path=str(ckpt_dir),
    )
    backend = FilesystemCheckpointBackend(ckpt_dir)

    s1_runs = 0
    s2_runs = 0

    async def _run(mgr: CheckpointManager) -> None:
        ctx = _context(pipeline_id=pipeline_id)
        executor = DAGExecutor(_nodes([("s1", set()), ("s2", {"s1"})]), ctx, mgr)

        async def fake_execute(node: StepNode) -> None:
            nonlocal s1_runs, s2_runs
            step = node.step

            async def handler_fn() -> StepOutput:
                nonlocal s1_runs, s2_runs
                if step.id == "s1":
                    s1_runs += 1
                    return StepOutput(raw=f"s1-run-{s1_runs}")
                if step.id == "s2":
                    s2_runs += 1
                    return stop(kind="CONSULT_PENDING", raw="stop-s2")
                raise AssertionError(f"unexpected step {step.id}")

            output = await execute_step_with_wrappers(
                step,
                handler_fn,
                checkpoint_manager=mgr,
            )
            executor._observability.record_success(node, output, 0.01)

        executor._execute_step = fake_execute  # type: ignore[method-assign]
        await executor.execute()

    bus1 = _RecordingEventBus()
    mgr1 = CheckpointManager(
        backend, config, pipeline_id, lineage_root, event_bus=bus1
    )
    await _run(mgr1)
    assert s1_runs == 1
    assert s2_runs == 1

    s1_key = f"{pipeline_id}:s1:{lineage_root}"
    s2_key = f"{pipeline_id}:s2:{lineage_root}"
    assert await backend.exists(s1_key)
    assert not await backend.exists(s2_key)

    bus2 = _RecordingEventBus()
    mgr2 = CheckpointManager(
        backend, config, pipeline_id, lineage_root, event_bus=bus2
    )
    await _run(mgr2)
    assert s1_runs == 1
    assert s2_runs == 2
    assert bus2.loaded_steps == ["s1"]
    assert not await backend.exists(s2_key)


def test_checkpoint_manager_none_without_config_or_resume() -> None:
    """Without checkpoint YAML or resume_of, preparation leaves manager unset."""
    executor = MagicMock()
    executor.proxy = MagicMock(event_bus=None)
    pipeline = PipelineSpec(
        id="plain-pipe",
        version="1",
        type="test",
        category="test",
        steps=[StepConfig(id="only", type="select_winner")],
        output="only",
        checkpoint=None,
    )
    context = MagicMock()
    context.original_request = {}

    assert (
        build_checkpoint_manager_if_needed(
            executor,
            pipeline,
            context,
            "exec-1",
        )
        is None
    )
