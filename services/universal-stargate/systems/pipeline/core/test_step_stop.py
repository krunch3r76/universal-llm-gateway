"""Step stop is a result field: unlaunched nodes skip, status stays completed.

Break covered here:
- a stop while a sibling is running must SKIP the sibling's pending dependent
  and still record the sibling output
- ``pipeline.output`` naming a step skipped by that stop yields the stopping
  step's raw
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo_root = str(Path(__file__).resolve().parents[5])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from .dag import StepNode, StepState  # noqa: E402
from .execution import DAGExecutor  # noqa: E402
from .execution.async_tracker import PipelineExecutionTracker  # noqa: E402
from .execution.dag_executor.observability.outcomes import record_success  # noqa: E402
from .execution.dispatch_journal import (  # noqa: E402
    fetch_terminal,
    initialize_schema,
    journal_terminal,
)
from .executor.outcome_assembly import assemble_outcome  # noqa: E402
from .executor.prepared import PreparedPipelineExecution  # noqa: E402
from .handlers.step_output import StepOutput, stop  # noqa: E402
from .schemas import PipelineSpec, StepConfig  # noqa: E402
from .step_controls import StepDefinitionError  # noqa: E402


def _step(step_id: str) -> StepConfig:
    return StepConfig(id=step_id, type="select_winner")


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


def _context(*, stops: list[str] | None = None) -> MagicMock:
    context = MagicMock()
    context.outputs = {}
    context.options = {}
    context.execution_id = "exec-stop"
    context.recorder = None
    context._step_model_override = {}
    context._step_progress_by_step = {}
    context._registry = MagicMock()
    context.pipeline = MagicMock()
    context.pipeline.id = "stop-pipe"
    context.pipeline.stops = stops
    context.drain_step_calls = lambda _name: []
    context.set_output = lambda step_id, output: context.outputs.__setitem__(
        step_id, output
    )
    context.get_output = lambda step_id: context.outputs.get(step_id)
    context._proxy = MagicMock()
    context._proxy.event_bus.publish_nowait = AsyncMock(return_value=None)
    return context


def _executor(
    spec: list[tuple[str, set[str]]], *, stops: list[str] | None = None
) -> DAGExecutor:
    return DAGExecutor(_nodes(spec), _context(stops=stops))


@pytest.mark.asyncio
async def test_stop_skips_dependents_and_tracker_keeps_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """a→b, a→c: b and c are never launched; status stays completed.

    Breaks when a stop promotes dependents to READY and the next pass launches
    them, or when the tracker status becomes something other than completed.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    executor = _executor([("a", set()), ("b", {"a"}), ("c", {"a"})])
    launched: list[str] = []

    async def fake_execute(node: StepNode) -> None:
        launched.append(node.step.id)
        executor._observability.record_success(
            node,
            stop(kind="CONSULT_PENDING", raw="from-a"),
            0.01,
        )

    executor._execute_step = fake_execute  # type: ignore[method-assign]
    await executor.execute()

    assert launched == ["a"]
    assert executor.nodes["b"].state == StepState.SKIPPED
    assert executor.nodes["c"].state == StepState.SKIPPED
    assert executor.stop is not None
    assert executor.stop.kind == "CONSULT_PENDING"

    pipeline = PipelineSpec(
        id="stop-pipe",
        version="1",
        type="test",
        category="test",
        steps=[_step("a"), _step("b"), _step("c")],
        output="b",
    )
    prepared = PreparedPipelineExecution(
        pipeline=pipeline,
        pipeline_context=executor.context,
        nodes=executor.nodes,
        steps=pipeline.steps,
        output_aliases={},
        text="",
        execution_id="exec-stop",
        dag_executor=executor,
        recorder=MagicMock(),
    )
    outcome = assemble_outcome(prepared, 0.1)
    assert outcome.content == "from-a"
    assert outcome.stop is not None
    assert outcome.stop.kind == "CONSULT_PENDING"

    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="exec-stop",
        pipeline="stop-pipe",
        started_at="2026-10-05T00:00:00Z",
    )
    tracker.complete_execution(
        "exec-stop",
        content=outcome.content,
        model=outcome.model,
        usage=outcome.usage,
        duration_s=outcome.duration_s,
        stop=outcome.stop,
    )
    record = tracker.get("exec-stop")
    assert record is not None
    body = record.to_dict()
    assert body["status"] == "completed"
    assert body["result"]["stop"]["kind"] == "CONSULT_PENDING"

    await journal_terminal(record)
    fetched = await fetch_terminal("exec-stop")
    assert fetched is not None
    assert fetched["result"]["stop"] == body["result"]["stop"]
    assert fetched["status"] == "completed"


@pytest.mark.asyncio
async def test_running_sibling_finishes_and_its_dependent_is_skipped() -> None:
    """Stop while sib is RUNNING: dep (pending on sib) is SKIPPED, sib is recorded.

    Breaks when the stop cancels the sibling, or when sib's completion readies
    dep after the stop and dep launches.
    """
    executor = _executor([("a", set()), ("sib", set()), ("dep", {"sib"})])
    launched: list[str] = []
    sib_entered = asyncio.Event()

    async def fake_execute(node: StepNode) -> None:
        launched.append(node.step.id)
        if node.step.id == "sib":
            sib_entered.set()
            await asyncio.sleep(0.05)
            executor._observability.record_success(
                node, StepOutput(raw="sib-out"), 0.05
            )
            return
        if node.step.id == "a":
            await sib_entered.wait()
            executor._observability.record_success(
                node, stop(kind="ROW_PINNED", raw="from-a"), 0.01
            )
            return
        raise AssertionError(f"launched {node.step.id}")

    executor._execute_step = fake_execute  # type: ignore[method-assign]
    await executor.execute()

    assert "dep" not in launched
    assert executor.nodes["dep"].state == StepState.SKIPPED
    assert executor.nodes["sib"].state == StepState.COMPLETED
    assert executor.context.outputs["sib"].raw == "sib-out"


@pytest.mark.asyncio
async def test_stop_during_model_lookup_does_not_launch_ready_dependent() -> None:
    """Stop lands while the scheduler awaits dep's model lookup.

    Breaks when launch_steps starts a READY step that filter already selected
    after executor.stop was set during that wait.
    """
    executor = _executor([("a", set()), ("sib", set()), ("dep", {"sib"})])
    launched: list[str] = []
    lookup_entered = asyncio.Event()
    release_lookup = asyncio.Event()
    real_resolve = executor._model_coordination.resolve_target_model

    async def resolve(node: StepNode) -> str | None:
        if node.step.id == "dep":
            lookup_entered.set()
            await release_lookup.wait()
        return await real_resolve(node)

    executor._model_coordination.resolve_target_model = resolve  # type: ignore[method-assign]

    async def fake_execute(node: StepNode) -> None:
        launched.append(node.step.id)
        if node.step.id == "sib":
            executor._observability.record_success(
                node, StepOutput(raw="sib-out"), 0.01
            )
            return
        if node.step.id == "a":
            await lookup_entered.wait()
            executor._observability.record_success(
                node, stop(kind="HOLD_MERGE", raw="from-a"), 0.01
            )
            release_lookup.set()
            return
        raise AssertionError(f"launched {node.step.id}")

    executor._execute_step = fake_execute  # type: ignore[method-assign]
    await executor.execute()

    assert set(launched) == {"a", "sib"}
    assert executor.nodes["dep"].state == StepState.SKIPPED
    assert executor.nodes["sib"].state == StepState.COMPLETED
    assert executor.stop is not None
    assert executor.stop.kind == "HOLD_MERGE"


@pytest.mark.asyncio
async def test_stop_kind_outside_declared_list_names_the_step() -> None:
    """A kind outside ``stops:`` raises StepDefinitionError naming the step.

    Breaks when an undeclared kind is stored on the executor, or when the
    error text omits the step id.
    """
    executor = _executor([("stopper", set())], stops=["CONFIRM_PENDING"])
    node = executor.nodes["stopper"]
    with pytest.raises(StepDefinitionError) as raised:
        record_success(
            executor._observability,
            node,
            stop(kind="CONSULT_PENDING", raw="nope"),
            0.0,
        )
    assert raised.value.step_name == "stopper"
    assert "for step stopper:" in str(raised.value)
    assert executor.stop is None
    assert node.state != StepState.COMPLETED


@pytest.mark.asyncio
async def test_undeclared_stops_accepts_any_kind() -> None:
    """No ``stops:`` list accepts a kind the engine does not special-case.

    Breaks when a hidden enum rejects ``hop_budget_*`` or a free-form kind.
    """
    executor = _executor([("a", set())], stops=None)
    record_success(
        executor._observability,
        executor.nodes["a"],
        stop(kind="hop_budget_custom", raw="budget"),
        0.0,
    )
    assert executor.stop is not None
    assert executor.stop.kind == "hop_budget_custom"
    assert executor.nodes["a"].state == StepState.COMPLETED
