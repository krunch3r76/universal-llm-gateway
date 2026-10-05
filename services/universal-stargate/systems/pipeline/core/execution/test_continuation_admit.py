"""Continuation admit: one claim, lineage pin, and memo replay across a stop."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from systems.pipeline.core.conditions import evaluate_condition
from systems.pipeline.core.dag import StepNode, StepState
from systems.pipeline.core.execution import DAGExecutor
from systems.pipeline.core.execution.checkpoint import FilesystemCheckpointBackend
from systems.pipeline.core.execution.dispatch_journal import (
    assess_continuation,
    pipeline_steps_sha256,
)
from systems.pipeline.core.execution.dispatch_journal_transitions import (
    write_lineage_root_sync,
    write_transition_sync,
)
from systems.pipeline.core.execution.step_wrapper import execute_step_with_wrappers
from systems.pipeline.core.executor.preparation import (
    build_checkpoint_manager_if_needed,
    do_prepare_execution,
)
from systems.pipeline.core.handlers.step_output import StepOutput, stop
from systems.pipeline.core.pipeline_config import PipelineSpec
from systems.pipeline.core.schemas import StepConfig
from systems.proxy.routers.api.pipelines_dispatch import (
    DispatchRequest,
    admit_dispatch,
    continuation_refusal_response,
)


def _pipeline(*, stops: list[str] | None = None) -> PipelineSpec:
    return PipelineSpec(
        id="stop-pipe",
        version="1",
        type="test",
        category="test",
        steps=[
            StepConfig(id="s1", type="select_winner"),
            StepConfig(id="s2", type="select_winner"),
        ],
        output="s2",
        stops=stops,
    )


def _journal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _path(tmp_path):
    from systems.pipeline.core.execution.dispatch_journal import _journal_path

    return _journal_path()


def _record(
    path,
    execution_id: str,
    *,
    status: str,
    record_json: dict,
    pipeline: str = "stop-pipe",
) -> None:
    write_transition_sync(
        path,
        execution_id=execution_id,
        pipeline=pipeline,
        status=status,
        caller_agent=None,
        started_at="2026-10-05T00:00:00Z",
        completed_at="2026-10-05T00:01:00Z",
        record_json=record_json,
    )


def _pin(
    path,
    pipeline: PipelineSpec,
    root_id: str,
    *,
    steps_sha256: str | None = None,
) -> None:
    write_lineage_root_sync(
        path,
        root_id=root_id,
        pipeline_id=pipeline.id,
        version=pipeline.version,
        steps_sha256=steps_sha256 or pipeline_steps_sha256(pipeline),
        source_text="root source",
        options_json=json.dumps(
            {
                "_lineage_request": True,
                "pipeline_options": {"log_dir": "/tmp"},
                "messages": [{"role": "user", "content": "root source"}],
            }
        ),
    )


def _stopped(path, execution_id: str = "root-1") -> None:
    _record(
        path,
        execution_id,
        status="completed",
        record_json={
            "execution_id": execution_id,
            "status": "completed",
            "result": {
                "content": "paused",
                "model": "m",
                "stop": {"kind": "CONSULT_PENDING", "reason": None, "payload": {}},
            },
        },
    )


@pytest.mark.asyncio
async def test_concurrent_admits_one_202_and_409_with_same_successor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Two continues of one stop: one claim wins.

    Breaks if the claim is a read-then-write.
    """
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    _pin(path, pipeline, "root-1")
    steps_hash = pipeline_steps_sha256(pipeline)

    first, second = await asyncio.gather(
        assess_continuation(
            stop_execution_id="root-1",
            successor_execution_id="succ-a",
            steps_sha256=steps_hash,
            pipeline_id=pipeline.id,
        ),
        assess_continuation(
            stop_execution_id="root-1",
            successor_execution_id="succ-b",
            steps_sha256=steps_hash,
            pipeline_id=pipeline.id,
        ),
    )
    decisions = [first, second]
    admitted = [item for item in decisions if item.admitted]
    refused = [item for item in decisions if not item.admitted]
    assert len(admitted) == 1
    assert admitted[0].http_status == 202
    assert continuation_refusal_response(admitted[0]) is None
    assert len(refused) == 1
    refusal = continuation_refusal_response(refused[0])
    assert refusal is not None
    assert refusal.status_code == 409
    body = json.loads(refusal.body)
    successor = body["error"]["data"]["successor_execution_id"]
    assert successor == admitted[0].successor_execution_id
    assert body["error"]["code"] == "continuation_claimed"


@pytest.mark.asyncio
async def test_completed_without_stop_is_not_resumable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline()
    path = _path(tmp_path)
    _record(
        path,
        "done-1",
        status="completed",
        record_json={
            "status": "completed",
            "result": {"content": "ok", "model": "m", "stop": None},
        },
    )
    decision = await assess_continuation(
        stop_execution_id="done-1",
        successor_execution_id="succ",
        steps_sha256=pipeline_steps_sha256(pipeline),
        pipeline_id=pipeline.id,
    )
    assert decision.http_status == 422
    assert decision.code == "not_resumable"
    refusal = continuation_refusal_response(decision)
    assert refusal is not None
    assert refusal.status_code == 422


@pytest.mark.asyncio
async def test_interrupted_by_restart_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _record(
        path,
        "orphan-1",
        status="failed",
        record_json={
            "status": "failed",
            "error": {
                "code": "interrupted_by_restart",
                "message": "Dispatch interrupted by process restart",
                "data": {"resumable": True},
            },
        },
    )
    _pin(path, pipeline, "orphan-1")
    decision = await assess_continuation(
        stop_execution_id="orphan-1",
        successor_execution_id="succ-restart",
        steps_sha256=pipeline_steps_sha256(pipeline),
        pipeline_id=pipeline.id,
    )
    assert decision.admitted is True
    assert decision.http_status == 202
    assert decision.successor_execution_id == "succ-restart"
    assert decision.lineage_root == "orphan-1"


@pytest.mark.asyncio
async def test_cancelled_run_is_422_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A cancelled run is refused with its own code, before any claim."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _record(
        path,
        "cancel-1",
        status="failed",
        record_json={
            "status": "failed",
            "error": {
                "code": "pipeline_execution_cancelled",
                "message": "Pipeline execution cancelled.",
            },
        },
    )
    _pin(path, pipeline, "cancel-1")
    decision = await assess_continuation(
        stop_execution_id="cancel-1",
        successor_execution_id="succ-cancel",
        steps_sha256=pipeline_steps_sha256(pipeline),
        pipeline_id=pipeline.id,
    )
    assert decision.http_status == 422
    assert decision.code == "cancelled"
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    connection.close()
    assert row is not None
    assert row[0] == 0


@pytest.mark.asyncio
async def test_steps_hash_drift_is_422(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path, "root-drift")
    _pin(path, pipeline, "root-drift", steps_sha256="0" * 64)
    decision = await assess_continuation(
        stop_execution_id="root-drift",
        successor_execution_id="succ-drift",
        steps_sha256=pipeline_steps_sha256(pipeline),
        pipeline_id=pipeline.id,
    )
    assert decision.http_status == 422
    assert decision.code == "lineage_spec_drift"


@pytest.mark.asyncio
async def test_continuation_context_lineage_root_and_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Continuation sees the root id, and conditions can read continuation_input."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _pin(path, pipeline, "root-1")
    _stopped(path, "root-1")
    executor = MagicMock()
    executor.registry.get_pipeline.return_value = pipeline
    executor.proxy = None
    executor._publish_event = MagicMock()
    log_dir = tmp_path / "logs"
    context = SimpleNamespace(
        selected_model=pipeline.id,
        original_request={
            "messages": [],
            "pipeline_options": {
                "resume_of": "root-1",
                "continuation_input": "released",
                "log_dir": str(log_dir),
            },
        },
        http_request=SimpleNamespace(state=SimpleNamespace()),
        selected_gateway_instance=None,
        chat_request=None,
    )
    prepared = do_prepare_execution(executor, context, execution_id="succ-1")
    assert prepared.pipeline_context.lineage_root == "root-1"
    assert prepared.pipeline_context.continuation_seq == 1
    assert prepared.pipeline_context.source_text == "root source"
    assert (
        prepared.pipeline_context.step_idempotency_key(pipeline.steps[0]) == "root-1:s1"
    )
    assert evaluate_condition(
        "options.get('continuation_input') == 'released'",
        {},
        prepared.pipeline_context.options,
    )


def _nodes() -> dict[str, StepNode]:
    spec = [("s1", set()), ("s2", {"s1"})]
    nodes = {
        step_id: StepNode(
            step=StepConfig(id=step_id, type="select_winner"),
            dependencies=set(deps),
            state=StepState.READY if not deps else StepState.PENDING,
        )
        for step_id, deps in spec
    }
    for step_id, deps in spec:
        for dep in deps:
            nodes[dep].dependents.add(step_id)
    return nodes


@pytest.mark.asyncio
async def test_stops_without_checkpoint_memo_skips_completed_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A root run that declares stops: writes the memo the continuation replays.

    Breaks when the manager is built only for checkpoint: or resume_of, so the
    first run of a stops: pipeline leaves nothing to reload.
    """
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    assert pipeline.checkpoint is None
    executor = MagicMock()
    executor.proxy = MagicMock(event_bus=None)
    root_request = SimpleNamespace(original_request={})
    manager = build_checkpoint_manager_if_needed(
        executor,
        pipeline,
        root_request,
        "root-exec",
    )
    assert manager is not None

    s1_runs = 0
    s2_runs = 0

    async def _run(mgr) -> None:
        ctx = MagicMock()
        ctx.outputs = {}
        ctx.options = {}
        ctx.execution_id = "exec"
        ctx.recorder = None
        ctx._step_model_override = {}
        ctx._step_progress_by_step = {}
        ctx.pipeline = pipeline
        ctx.drain_step_calls = lambda _name: []
        ctx.set_output = lambda step_id, output: ctx.outputs.__setitem__(
            step_id, output
        )
        ctx.get_output = lambda step_id: ctx.outputs.get(step_id)
        ctx._proxy = MagicMock()
        ctx._proxy.event_bus.publish_nowait = AsyncMock(return_value=None)
        dag = DAGExecutor(_nodes(), ctx, mgr)

        async def fake_execute(node: StepNode) -> None:
            nonlocal s1_runs, s2_runs
            step = node.step

            async def handler_fn() -> StepOutput:
                nonlocal s1_runs, s2_runs
                if step.id == "s1":
                    s1_runs += 1
                    return StepOutput(raw=f"s1-{s1_runs}")
                if step.id == "s2":
                    s2_runs += 1
                    return stop(kind="CONSULT_PENDING", raw="stop")
                raise AssertionError(step.id)

            output = await execute_step_with_wrappers(
                step,
                handler_fn,
                checkpoint_manager=mgr,
            )
            dag._observability.record_success(node, output, 0.01)

        dag._execute_step = fake_execute  # type: ignore[method-assign]
        await dag.execute()

    await _run(manager)
    assert s1_runs == 1
    assert s2_runs == 1
    backend = FilesystemCheckpointBackend(manager._config.storage_path)
    assert await backend.exists(f"{pipeline.id}:s1:root-exec")
    assert not await backend.exists(f"{pipeline.id}:s2:root-exec")

    continuation = SimpleNamespace(
        original_request={"pipeline_options": {"resume_of": "root-exec"}},
    )
    continued = build_checkpoint_manager_if_needed(
        executor,
        pipeline,
        continuation,
        "succ-exec",
    )
    assert continued is not None
    await _run(continued)
    assert s1_runs == 1
    assert s2_runs == 2


@pytest.mark.asyncio
async def test_admit_dispatch_maps_409_and_does_not_register_the_loser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """HTTP admit: the losing continue is 409 and never registers a tracker row."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    _pin(path, pipeline, "root-1")

    proxy = MagicMock()
    proxy.is_pipeline_system_ready = True
    proxy.pipeline_registry.is_pipeline.return_value = True
    proxy.pipeline_registry.get_pipeline.return_value = pipeline
    proxy.pipeline_executor.generate_execution_id.side_effect = ["succ-a", "succ-b"]
    proxy.pipeline_executor.execute_async = AsyncMock()
    proxy.request_preparer.prepare_request = AsyncMock(return_value=SimpleNamespace())
    tracker = MagicMock()
    proxy.pipeline_dispatch_tracker = tracker

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/capabilities/test/stop-pipe",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    request = Request(scope)

    body = DispatchRequest(
        model="stop-pipe",
        messages=[{"role": "user", "content": "continue"}],
        pipeline_options={"resume_of": "root-1", "continuation_input": "go"},
    )
    first, second = await asyncio.gather(
        admit_dispatch(request, proxy, body),
        admit_dispatch(request, proxy, body),
    )
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [202, 409]
    loser = first if first.status_code == 409 else second
    winner = second if loser is first else first
    loser_body = json.loads(loser.body)
    winner_body = json.loads(winner.body)
    assert (
        loser_body["error"]["data"]["successor_execution_id"]
        == winner_body["execution_id"]
    )
    assert tracker.register_execution.call_count == 1


def _executor(pipeline: PipelineSpec) -> MagicMock:
    executor = MagicMock()
    executor.registry.get_pipeline.return_value = pipeline
    executor.proxy = None
    executor._publish_event = MagicMock()
    executor.request_executor = None
    return executor


def _continue_context(
    pipeline: PipelineSpec,
    tmp_path,
    **options: object,
) -> SimpleNamespace:
    log_dir = tmp_path / "logs"
    pipeline_options = {"log_dir": str(log_dir), **options}
    return SimpleNamespace(
        selected_model=pipeline.id,
        original_request={
            "messages": [{"role": "user", "content": "continue"}],
            "pipeline_options": pipeline_options,
        },
        http_request=SimpleNamespace(state=SimpleNamespace()),
        selected_gateway_instance=None,
        chat_request=None,
    )


def test_chat_prepare_one_claim_second_resume_is_409(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The sync chat path claims in prepare. A second resume of the same stop is 409."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    _pin(path, pipeline, "root-1")
    executor = _executor(pipeline)
    from systems.pipeline.core.execution.dispatch_journal_transitions import (
        ContinuationRefusedError,
    )

    do_prepare_execution(
        executor,
        _continue_context(pipeline, tmp_path, resume_of="root-1"),
        execution_id="succ-chat",
    )
    with pytest.raises(ContinuationRefusedError) as caught:
        do_prepare_execution(
            executor,
            _continue_context(pipeline, tmp_path, resume_of="root-1"),
            execution_id="succ-other",
        )
    assert caught.value.decision.http_status == 409
    assert caught.value.decision.successor_execution_id == "succ-chat"
    connection = sqlite3.connect(path)
    count = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    connection.close()
    assert count is not None
    assert count[0] == 1


@pytest.mark.asyncio
async def test_chat_execute_maps_cancelled_to_422(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A chat resume of a cancelled run is 422 and does not insert a claim."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _record(
        path,
        "cancel-1",
        status="failed",
        record_json={
            "status": "failed",
            "error": {"code": "pipeline_execution_cancelled", "message": "no"},
        },
    )
    _pin(path, pipeline, "cancel-1")
    from systems.pipeline.core.executor.pipeline_executor import PipelineExecutor

    executor = _executor(pipeline)
    executor.generate_execution_id.return_value = "succ-cancel-chat"
    executor.prepare_execution.side_effect = (
        lambda context, *, execution_id, journal_backed=False: do_prepare_execution(
            executor,
            context,
            execution_id=execution_id,
            journal_backed=journal_backed,
        )
    )
    context = _continue_context(pipeline, tmp_path, resume_of="cancel-1")
    with pytest.raises(HTTPException) as caught:
        await PipelineExecutor.execute(executor, context)
    assert caught.value.status_code == 422
    assert caught.value.detail["error"]["code"] == "cancelled"
    connection = sqlite3.connect(path)
    count = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    connection.close()
    assert count is not None
    assert count[0] == 0


def test_same_successor_prepare_after_admit_is_not_a_second_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Dispatch claims, then prepare runs again for that same successor."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    _pin(path, pipeline, "root-1")

    async def _admit() -> None:
        decision = await assess_continuation(
            stop_execution_id="root-1",
            successor_execution_id="succ-1",
            steps_sha256=pipeline_steps_sha256(pipeline),
            pipeline_id=pipeline.id,
        )
        assert decision.admitted
        assert decision.fresh_claim is True

    asyncio.run(_admit())
    prepared = do_prepare_execution(
        _executor(pipeline),
        _continue_context(pipeline, tmp_path, resume_of="root-1"),
        execution_id="succ-1",
    )
    assert prepared.pipeline_context.execution_id == "succ-1"
    connection = sqlite3.connect(path)
    count = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    connection.close()
    assert count is not None
    assert count[0] == 1


def test_inherited_model_sets_chat_completions_only_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Root model survives a continuation that omits it."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    write_lineage_root_sync(
        path,
        root_id="root-1",
        pipeline_id=pipeline.id,
        version=pipeline.version,
        steps_sha256=pipeline_steps_sha256(pipeline),
        source_text="root source",
        options_json=json.dumps(
            {
                "_lineage_request": True,
                "pipeline_options": {"model": "openai/gpt-5-search-api"},
                "messages": [{"role": "user", "content": "root source"}],
            }
        ),
    )
    prepared = do_prepare_execution(
        _executor(pipeline),
        _continue_context(pipeline, tmp_path, resume_of="root-1"),
        execution_id="succ-model",
    )
    assert prepared.pipeline_context.options["model"] == "openai/gpt-5-search-api"
    assert prepared.pipeline_context.options["chat_completions_only"] is True
    assert prepared.pipeline_context.source_text == "root source"
    assert prepared.pipeline_context._messages == [
        {"role": "user", "content": "root source"}
    ]
    assert prepared.pipeline_context.options["continuation_input"] == [
        {"role": "user", "content": "continue"}
    ]


def test_root_pin_only_when_the_run_is_journal_backed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A chat root writes no lineage row. A dispatch root does."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline()
    path = _path(tmp_path)
    context = SimpleNamespace(
        selected_model=pipeline.id,
        original_request={
            "messages": [{"role": "user", "content": "root"}],
            "pipeline_options": {"log_dir": str(tmp_path / "logs")},
        },
        http_request=SimpleNamespace(state=SimpleNamespace()),
        selected_gateway_instance=None,
        chat_request=None,
    )
    do_prepare_execution(_executor(pipeline), context, execution_id="chat-root")
    connection = sqlite3.connect(path)
    chat_table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_lineage'"
    ).fetchone()
    connection.close()
    assert chat_table is None
    do_prepare_execution(
        _executor(pipeline),
        context,
        execution_id="dispatch-root",
        journal_backed=True,
    )
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT root_id FROM pipeline_lineage"
    ).fetchone()
    connection.close()
    assert row == ("dispatch-root",)


def _admit_proxy(pipeline: PipelineSpec):
    proxy = MagicMock()
    proxy.is_pipeline_system_ready = True
    proxy.pipeline_registry.is_pipeline.return_value = True
    proxy.pipeline_registry.get_pipeline.return_value = pipeline
    proxy.pipeline_executor.generate_execution_id.return_value = "succ-fail"
    proxy.pipeline_executor.execute_async = AsyncMock()
    proxy.pipeline_dispatch_tracker = MagicMock()
    return proxy


def _admit_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/capabilities/test/stop-pipe",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_capacity_failure_releases_the_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A 503 after the insert must not leave the stop claimed."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    _pin(path, pipeline, "root-1")
    proxy = _admit_proxy(pipeline)
    from systems.pipeline.core.execution.async_tracker import TrackerCapacityError

    proxy.pipeline_dispatch_tracker.register_execution.side_effect = (
        TrackerCapacityError("full")
    )
    body = DispatchRequest(
        model="stop-pipe",
        messages=[{"role": "user", "content": "continue"}],
        pipeline_options={"resume_of": "root-1"},
    )
    response = await admit_dispatch(_admit_request(), proxy, body)
    assert response.status_code == 503
    connection = sqlite3.connect(path)
    count = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    connection.close()
    assert count is not None
    assert count[0] == 0


@pytest.mark.asyncio
async def test_prepare_failure_releases_the_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A 500 from request prepare releases the claim this successor inserted."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _stopped(path)
    _pin(path, pipeline, "root-1")
    proxy = _admit_proxy(pipeline)
    proxy.request_preparer.prepare_request = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    body = DispatchRequest(
        model="stop-pipe",
        messages=[{"role": "user", "content": "continue"}],
        pipeline_options={"resume_of": "root-1"},
    )
    response = await admit_dispatch(_admit_request(), proxy, body)
    assert response.status_code == 500
    proxy.pipeline_dispatch_tracker.fail_execution.assert_called_once()
    connection = sqlite3.connect(path)
    count = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    connection.close()
    assert count is not None
    assert count[0] == 0


def test_prune_drops_claims_and_lineage_with_the_journal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Retention for the new tables is the journal's completed_at clock."""
    _journal(monkeypatch, tmp_path)
    pipeline = _pipeline(stops=["CONSULT_PENDING"])
    path = _path(tmp_path)
    _record(
        path,
        "root-old",
        status="completed",
        record_json={
            "status": "completed",
            "result": {"stop": {"kind": "CONSULT_PENDING"}},
        },
    )
    # write_transition stamps a fresh completed_at. Force the retention clock back.
    connection = sqlite3.connect(path)
    connection.execute(
        """
        UPDATE dispatch_records
        SET completed_at_epoch = ?
        WHERE execution_id = 'root-old'
        """,
        (1.0,),
    )
    connection.commit()
    connection.close()
    _pin(path, pipeline, "root-old")

    async def _claim() -> None:
        decision = await assess_continuation(
            stop_execution_id="root-old",
            successor_execution_id="succ-old",
            steps_sha256=pipeline_steps_sha256(pipeline),
            pipeline_id=pipeline.id,
        )
        assert decision.admitted

    asyncio.run(_claim())
    from systems.pipeline.core.execution.dispatch_journal import _prune_sync

    _prune_sync(path, retention_seconds=3600)
    connection = sqlite3.connect(path)
    claims = connection.execute(
        "SELECT COUNT(*) FROM pipeline_continuation_claims"
    ).fetchone()
    pins = connection.execute(
        "SELECT COUNT(*) FROM pipeline_lineage WHERE root_id = 'root-old'"
    ).fetchone()
    connection.close()
    assert claims is not None and claims[0] == 0
    assert pins is not None and pins[0] == 0
