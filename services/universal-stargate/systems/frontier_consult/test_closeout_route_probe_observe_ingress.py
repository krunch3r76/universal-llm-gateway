"""AC1–AC3 ingress and AC5 job-boundary tests for closeout route-probe observe."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import functools
import json
import threading
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from .route_implement import ImplementCloseoutBody, implement_closeout

C: dict[str, Any] = {
    "schema_version": 1,
    "status": "complete",
    "summary": "s",
    "files_modified": ["libs/claude_bundles/holder_strings.py"],
    "propagation": [{"service": "cdp_ask", "code_ref": "HEAD"}],
    "verification": [
        {
            "command": "gate_d:deliverable",
            "exit_code": 0,
            "exit_code_register": "derived",
            "basis": "gate_d_boolean_pass",
        },
        {
            "command": "ruff check libs/claude_bundles/holder_strings.py",
            "exit_code": 0,
            "exit_code_register": "observed",
            "invocation_id": "proc:lint1",
        },
    ],
}


def _mock_proxy_recording_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[asyncio.AbstractEventLoop]]:
    # Body copied from test_admission_generate._mock_proxy_recording_publish (L47–61).
    events: list[Any] = []
    publish_loops: list[asyncio.AbstractEventLoop] = []
    proxy = MagicMock()

    def _publish_from_sync(event: object) -> None:
        publish_loops.append(asyncio.get_running_loop())
        events.append(event)

    proxy.event_bus = MagicMock()
    proxy.event_bus.publish_from_sync = _publish_from_sync
    monkeypatch.setattr("systems.proxy.dependencies.get_proxy", lambda: proxy)
    return events, publish_loops


def _route_probe_events(events: list[Any]) -> list[Any]:
    return [
        e
        for e in events
        if getattr(e, "signal", "").startswith("frontier.closeout.routeprobe.")
    ]


class _ObserveExecutor(concurrent.futures.ThreadPoolExecutor):
    def __init__(self, *, hold: bool) -> None:
        super().__init__(max_workers=4)
        self.hold = hold
        self.captured: list[functools.partial] = []
        self.entered = threading.Event()
        self.gate = threading.Event()

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any):  # type: ignore[override]
        if (
            isinstance(fn, functools.partial)
            and getattr(fn.func, "__name__", "") == "run_closeout_route_probe_observe"
        ):
            self.captured.append(fn)
            if self.hold:

                def _wrapper() -> Any:
                    self.entered.set()
                    self.gate.wait()
                    return fn()

                return super().submit(_wrapper)
            return super().submit(fn)
        return super().submit(fn, *args, **kwargs)


@pytest_asyncio.fixture
async def observe_executor(
    request: pytest.FixtureRequest,
) -> Iterator[_ObserveExecutor]:
    hold = bool(getattr(request, "param", False))
    ex = _ObserveExecutor(hold=hold)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ex)
    yield ex
    ex.gate.set()
    if not ex._shutdown:
        ex.shutdown(wait=True)


async def _drain_observe_job(ex: _ObserveExecutor) -> None:
    if not ex._shutdown:
        ex.shutdown(wait=True)
    await asyncio.sleep(0)


def _status_payload(result: Any) -> tuple[int, dict[str, Any]]:
    if hasattr(result, "status_code"):
        return result.status_code, json.loads(result.body)
    return 200, result


@pytest.mark.asyncio
async def test_refusable_closeout_is_observed_and_untouched(
    monkeypatch: pytest.MonkeyPatch,
    observe_executor: _ObserveExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    before = copy.deepcopy(C)
    body = ImplementCloseoutBody(closeout=C, source_ref="workspaces://a/b.md")

    result = await implement_closeout(body)
    await _drain_observe_job(observe_executor)
    status, payload = _status_payload(result)

    rp = _route_probe_events(events)
    assert len(rp) == 1
    assert rp[0].signal == "frontier.closeout.routeprobe.evaluated"
    assert not any(e.signal.endswith("check.failed") for e in rp)

    pl = rp[0].payload
    assert pl["mode"] == "observe"
    assert pl["ingress"] == "unkeyed"
    assert pl["would_require"] is True
    assert pl["satisfied"] is False
    assert pl["would_have_refused"] is True
    assert "named" in pl["arms"]
    assert pl["required"][0]["slug"] == "cdp_ask"
    assert pl["required"][0]["named_by"] == ["row:untagged"]
    assert pl["missing"][0]["reason_code"] == "no_candidate_row"
    assert len(pl["residue_lines"]) == 1
    assert pl["residue_lines"][0].startswith("route_probe_observe: cdp_ask — would_require")

    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"
    assert C == before

    assert len(observe_executor.captured) == 1
    job = observe_executor.captured[0].keywords["closeout"]
    assert job == body.closeout
    assert job is not body.closeout
    assert job["verification"] is not body.closeout["verification"]
    assert job["verification"][0] is not body.closeout["verification"][0]


@pytest.mark.asyncio
async def test_observed_run_with_stderr_redirect_satisfies(
    monkeypatch: pytest.MonkeyPatch,
    observe_executor: _ObserveExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    closeout = copy.deepcopy(C)
    closeout["verification"].append(
        {
            "command": "pytest libs/cdp_ask/test_active_work.py -q 2>&1",
            "exit_code": 0,
            "exit_code_register": "observed",
            "invocation_id": "test:t1",
        }
    )
    status, payload = _status_payload(
        await implement_closeout(
            ImplementCloseoutBody(closeout=closeout, source_ref="workspaces://a/b.md")
        )
    )
    await _drain_observe_job(observe_executor)
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"
    rp = _route_probe_events(events)
    assert len(rp) == 1
    pl = rp[0].payload
    assert pl["would_require"] is True
    assert pl["satisfied"] is True
    assert pl["would_have_refused"] is False
    assert pl["residue_lines"] == []
    assert pl["required"][0]["evidence_invocation_id"] == "test:t1"


@pytest.mark.asyncio
async def test_reach_alone_requires(
    monkeypatch: pytest.MonkeyPatch,
    observe_executor: _ObserveExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    closeout = copy.deepcopy(C)
    del closeout["propagation"]
    status, payload = _status_payload(
        await implement_closeout(
            ImplementCloseoutBody(closeout=closeout, source_ref="workspaces://a/b.md")
        )
    )
    await _drain_observe_job(observe_executor)
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"
    rp = _route_probe_events(events)
    assert len(rp) == 1
    pl = rp[0].payload
    assert pl["would_require"] is True
    assert pl["arms"] == ["reached"]
    assert pl["required"][0]["reached_via"] == "libs/claude_bundles/holder_strings.py"
    assert pl["reach_errors"] == []


@pytest.mark.asyncio
async def test_unrelated_closeout_requires_nothing(
    monkeypatch: pytest.MonkeyPatch,
    observe_executor: _ObserveExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    closeout = copy.deepcopy(C)
    closeout["files_modified"] = ["docs/readme.md"]
    del closeout["propagation"]
    status, payload = _status_payload(
        await implement_closeout(
            ImplementCloseoutBody(closeout=closeout, source_ref="workspaces://a/b.md")
        )
    )
    await _drain_observe_job(observe_executor)
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"
    rp = _route_probe_events(events)
    assert len(rp) == 1
    pl = rp[0].payload
    assert pl["would_require"] is False
    assert pl["satisfied"] is None
    assert pl["required"] == []


@pytest.mark.asyncio
async def test_two_calls_two_events_one_digest(
    monkeypatch: pytest.MonkeyPatch,
    observe_executor: _ObserveExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    body = ImplementCloseoutBody(closeout=copy.deepcopy(C), source_ref="workspaces://a/b.md")
    await implement_closeout(body)
    await asyncio.sleep(0.05)
    await implement_closeout(body)
    await _drain_observe_job(observe_executor)
    rp = _route_probe_events(events)
    assert len(rp) == 2
    assert rp[0].payload["request_id"] != rp[1].payload["request_id"]
    assert rp[0].payload["closeout_digest"] == rp[1].payload["closeout_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("observe_executor", [True], indirect=True)
async def test_handler_returns_while_check_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    observe_executor: _ObserveExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    body = ImplementCloseoutBody(closeout=copy.deepcopy(C), source_ref="workspaces://a/b.md")
    result = await asyncio.wait_for(implement_closeout(body), timeout=5)
    status, payload = _status_payload(result)
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"

    assert await asyncio.to_thread(observe_executor.entered.wait, 5.0)
    assert not observe_executor.gate.is_set()
    assert _route_probe_events(events) == []

    observe_executor.gate.set()
    observe_executor.shutdown(wait=True)
    await asyncio.sleep(0)
    assert len(_route_probe_events(events)) == 1


@pytest.mark.asyncio
async def test_job_evaluate_fault_publishes_check_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    from . import closeout_route_probe_observe as mod

    def _boom(_: Any) -> Any:
        raise RuntimeError("boom")

    mod.run_closeout_route_probe_observe(
        request_id="r1",
        idempotency_key=None,
        source_ref="workspaces://a/b.md",
        closeout=C,
        evaluate=_boom,
    )
    rp = _route_probe_events(events)
    assert len(rp) == 1
    assert rp[0].signal == "frontier.closeout.routeprobe.check.failed"
    pl = rp[0].payload
    assert pl["stage"] == "evaluate"
    assert pl["error_type"] == "RuntimeError"
    assert pl["error"] == "boom"
    assert pl["ingress"] == "unkeyed"
    assert pl["mode"] == "observe"


@pytest.mark.asyncio
async def test_job_bad_verdict_is_emit_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    from . import closeout_route_probe_observe as mod

    mod.run_closeout_route_probe_observe(
        request_id="r1",
        idempotency_key=None,
        source_ref="workspaces://a/b.md",
        closeout=C,
        evaluate=lambda _: object(),  # type: ignore[return-value]
    )
    rp = _route_probe_events(events)
    assert len(rp) == 1
    assert rp[0].signal == "frontier.closeout.routeprobe.check.failed"
    assert rp[0].payload["stage"] == "emit"


@pytest.mark.asyncio
async def test_schedule_copy_fault_publishes_check_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    from . import closeout_route_probe_observe as mod

    class _Uncopyable(dict[str, Any]):
        def __deepcopy__(self, memo: Any) -> Any:
            raise RuntimeError("copy failed")

    mod.schedule_closeout_route_probe_observe(
        request_id="r2",
        idempotency_key="k",
        source_ref="todo:x",
        closeout=_Uncopyable(schema_version=1, status="complete", x=1),
    )
    await asyncio.sleep(0.2)
    rp = _route_probe_events(events)
    assert len(rp) == 1
    assert rp[0].signal == "frontier.closeout.routeprobe.check.failed"
    assert rp[0].payload["stage"] == "schedule"
    assert rp[0].payload["ingress"] == "keyed"


@pytest.mark.asyncio
async def test_schedule_without_loop_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    from . import closeout_route_probe_observe as mod

    await asyncio.to_thread(
        mod.schedule_closeout_route_probe_observe,
        request_id="r3",
        idempotency_key=None,
        source_ref="todo:x",
        closeout={"schema_version": 1, "status": "complete"},
    )
    assert _route_probe_events(events) == []


@pytest.mark.asyncio
async def test_job_closed_loop_failure_emit_is_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    from . import closeout_route_probe_observe as mod

    loop2 = asyncio.new_event_loop()
    loop2.close()

    def _boom(_: Any) -> Any:
        raise RuntimeError("boom")

    mod.run_closeout_route_probe_observe(
        request_id="r1",
        idempotency_key=None,
        source_ref="workspaces://a/b.md",
        closeout=C,
        evaluate=_boom,
        loop=loop2,
    )
    assert _route_probe_events(events) == []