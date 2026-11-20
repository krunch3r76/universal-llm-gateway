"""AC1–AC3 ingress and AC5 job-boundary tests for closeout route-probe observe."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import functools
import inspect
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from . import closeout_route_probe_observe as observe_mod
from . import route_implement as route_implement_mod
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

_OBSERVE_THREAD_PREFIX = "closeout_route_probe_observe"


def _mock_proxy_recording_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], list[asyncio.AbstractEventLoop]]:
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


class _ObserveDedicatedExecutor(concurrent.futures.ThreadPoolExecutor):
    def __init__(self, *, hold: bool) -> None:
        super().__init__(max_workers=1, thread_name_prefix="test_observe_double")
        self.hold = hold
        self.captured: list[functools.partial] = []
        self.entered = threading.Event()
        self.gate = threading.Event()

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any):  # type: ignore[override]
        if (
            isinstance(fn, functools.partial)
            and getattr(fn.func, "__name__", "") == "_run_observe_wrapped"
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
async def dedicated_observe_executor(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[_ObserveDedicatedExecutor]:
    ex = _ObserveDedicatedExecutor(hold=bool(getattr(request, "param", False)))

    def _schedule_with_ex(**kwargs: Any) -> None:
        observe_mod.schedule_closeout_route_probe_observe(**kwargs, observe_executor=ex)

    monkeypatch.setattr(
        route_implement_mod, "schedule_closeout_route_probe_observe", _schedule_with_ex
    )
    yield ex
    ex.gate.set()
    ex.shutdown(wait=True)


async def _wait_route_probe_events(
    events: list[Any], *, n: int, timeout_s: float = 10.0
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while len(_route_probe_events(events)) < n:
        if loop.time() >= deadline:
            raise AssertionError(
                f"expected {n} route-probe events, got {len(_route_probe_events(events))}"
            )
        await asyncio.sleep(0.01)


async def _wait_observe_settled(
    events: list[Any], *, n: int, timeout_s: float = 10.0
) -> None:
    await _wait_route_probe_events(events, n=n, timeout_s=timeout_s)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while observe_mod._observe_in_flight:
        if loop.time() >= deadline:
            raise AssertionError("closeout_route_probe_observe._observe_in_flight stuck True")
        await asyncio.sleep(0.01)


def _wait_reservation_clear_sync(timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while getattr(observe_mod, "_observe_in_flight", False):
        if time.monotonic() >= deadline:
            pytest.fail("closeout_route_probe_observe._observe_in_flight stuck True")
        time.sleep(0.01)


@pytest.fixture(autouse=True)
def observe_reservation_clear() -> Iterator[None]:
    _wait_reservation_clear_sync()
    yield
    _wait_reservation_clear_sync()


def _status_payload(result: Any) -> tuple[int, dict[str, Any]]:
    if hasattr(result, "status_code"):
        return result.status_code, json.loads(result.body)
    return 200, result


@pytest.mark.asyncio
async def test_refusable_closeout_is_observed_and_untouched(
    monkeypatch: pytest.MonkeyPatch,
    dedicated_observe_executor: _ObserveDedicatedExecutor,
) -> None:
    events, publish_loops = _mock_proxy_recording_publish(monkeypatch)
    before = copy.deepcopy(C)
    body = ImplementCloseoutBody(closeout=C, source_ref="workspaces://a/b.md")

    result = await implement_closeout(body)
    await _wait_observe_settled(events, n=1)
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
    assert body.closeout == before

    assert len(dedicated_observe_executor.captured) == 1
    job = dedicated_observe_executor.captured[0].keywords["closeout"]
    assert job == body.closeout
    assert job is not body.closeout
    assert job["verification"] is not body.closeout["verification"]
    assert job["verification"][0] is not body.closeout["verification"][0]

    assert [
        loop_
        for loop_, e in zip(publish_loops, events)
        if getattr(e, "signal", "").startswith("frontier.closeout.routeprobe.")
    ] == [asyncio.get_running_loop()]


@pytest.mark.asyncio
async def test_observed_run_with_stderr_redirect_satisfies(
    monkeypatch: pytest.MonkeyPatch,
    dedicated_observe_executor: _ObserveDedicatedExecutor,
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
    await _wait_observe_settled(events, n=1)
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
    dedicated_observe_executor: _ObserveDedicatedExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    closeout = copy.deepcopy(C)
    del closeout["propagation"]
    status, payload = _status_payload(
        await implement_closeout(
            ImplementCloseoutBody(closeout=closeout, source_ref="workspaces://a/b.md")
        )
    )
    await _wait_observe_settled(events, n=1)
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
    dedicated_observe_executor: _ObserveDedicatedExecutor,
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
    await _wait_observe_settled(events, n=1)
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
    dedicated_observe_executor: _ObserveDedicatedExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    body = ImplementCloseoutBody(closeout=copy.deepcopy(C), source_ref="workspaces://a/b.md")
    for n in (1, 2):
        status, payload = _status_payload(await implement_closeout(body))
        assert status == 422
        assert payload["error"]["code"] == "deploy_state_source_unresolvable"
        await _wait_observe_settled(events, n=n)
    rp = _route_probe_events(events)
    assert [e.signal for e in rp] == ["frontier.closeout.routeprobe.evaluated"] * 2
    assert rp[0].payload["request_id"] != rp[1].payload["request_id"]
    assert rp[0].payload["closeout_digest"] == rp[1].payload["closeout_digest"]
    assert len(dedicated_observe_executor.captured) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("dedicated_observe_executor", [True], indirect=True)
async def test_handler_returns_while_check_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    dedicated_observe_executor: _ObserveDedicatedExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    body = ImplementCloseoutBody(closeout=copy.deepcopy(C), source_ref="workspaces://a/b.md")
    result = await asyncio.wait_for(implement_closeout(body), timeout=5)
    status, payload = _status_payload(result)
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"

    assert await asyncio.to_thread(dedicated_observe_executor.entered.wait, 5.0)
    assert _route_probe_events(events) == []

    dedicated_observe_executor.gate.set()
    await _wait_observe_settled(events, n=1)
    assert len(_route_probe_events(events)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dedicated_observe_executor", [True], indirect=True)
async def test_second_closeout_drops_observe_when_executor_busy(
    monkeypatch: pytest.MonkeyPatch,
    dedicated_observe_executor: _ObserveDedicatedExecutor,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    ex = dedicated_observe_executor
    dropped_before = observe_mod._observe_dropped_total
    body = ImplementCloseoutBody(closeout=copy.deepcopy(C), source_ref="workspaces://a/b.md")
    s1, p1 = _status_payload(await implement_closeout(body))
    assert await asyncio.to_thread(ex.entered.wait, 5.0)
    s2, p2 = _status_payload(await implement_closeout(body))
    assert (s1, p1["error"]["code"]) == (422, "deploy_state_source_unresolvable")
    assert (s2, p2["error"]["code"]) == (422, "deploy_state_source_unresolvable")
    assert len(ex.captured) == 1
    drops = [
        e
        for e in _route_probe_events(events)
        if e.signal == "frontier.closeout.routeprobe.check.failed"
        and e.payload["error_type"] == "ObserveJobDropped"
    ]
    assert len(drops) == 1
    assert drops[0].payload["stage"] == "schedule"
    assert drops[0].payload["error"] == "observe executor saturated"
    assert observe_mod._observe_dropped_total == dropped_before + 1
    ex.gate.set()
    await _wait_observe_settled(events, n=2)
    assert [e.signal for e in _route_probe_events(events)].count(
        "frontier.closeout.routeprobe.evaluated"
    ) == 1


@pytest.mark.asyncio
async def test_production_closeout_uses_dedicated_observe_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)
    body = ImplementCloseoutBody(closeout=copy.deepcopy(C), source_ref="workspaces://a/b.md")
    status, payload = _status_payload(await implement_closeout(body))
    assert status == 422
    assert payload["error"]["code"] == "deploy_state_source_unresolvable"
    await _wait_route_probe_events(events, n=1)
    assert [e.signal for e in _route_probe_events(events)] == [
        "frontier.closeout.routeprobe.evaluated"
    ]
    workers = [
        t.name for t in threading.enumerate() if t.name.startswith(_OBSERVE_THREAD_PREFIX)
    ]
    assert workers, (
        f"no {_OBSERVE_THREAD_PREFIX} worker thread; "
        f"threads={sorted(t.name for t in threading.enumerate())}"
    )
    await _wait_observe_settled(events, n=1)


def test_observe_in_flight_set_only_in_scheduler() -> None:
    src_s = inspect.getsource(observe_mod.schedule_closeout_route_probe_observe)
    src_w = inspect.getsource(observe_mod._run_observe_wrapped)
    assert src_s.count("_observe_in_flight = True") == 1
    assert "_observe_in_flight = True" not in src_w
    assert "_observe_in_flight = False" in src_w


def test_gate_b_docstring_contract() -> None:
    from implement_admission import closeout_route_probe_gate as gate_mod
    from implement_admission import route_probe_command_parse as parse_mod

    from . import closeout_route_probe_observe_events as events_mod

    s = "observe-only: never refuses, rewrites, delays or fails a closeout"
    for mod in (gate_mod, parse_mod, observe_mod, events_mod):
        assert s in (inspect.getdoc(mod) or ""), mod.__name__
    assert "files_untracked_or_ignored" in (inspect.getdoc(gate_mod) or "")
    for fn in (
        observe_mod.schedule_closeout_route_probe_observe,
        observe_mod._run_observe_wrapped,
        observe_mod.run_closeout_route_probe_observe,
    ):
        assert s in (inspect.getdoc(fn) or ""), fn.__name__
    for fn in (
        gate_mod.make_route_probe_reach,
        gate_mod.evaluate_closeout_route_probes,
        parse_mod.parse_route_probe_command,
        observe_mod.residue_lines_for,
        events_mod.emit_closeout_routeprobe_evaluated,
        events_mod.emit_closeout_routeprobe_check_failed,
        events_mod.FrontierCloseoutRouteprobeEvaluated,
        events_mod.FrontierCloseoutRouteprobeCheckFailed,
    ):
        assert (inspect.getdoc(fn) or "").strip(), fn.__name__


@pytest.mark.asyncio
async def test_job_evaluate_fault_publishes_check_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, _ = _mock_proxy_recording_publish(monkeypatch)

    def _boom(_: Any) -> Any:
        raise RuntimeError("boom")

    observe_mod.run_closeout_route_probe_observe(
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

    observe_mod.run_closeout_route_probe_observe(
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

    class _Uncopyable(dict[str, Any]):
        def __deepcopy__(self, memo: Any) -> Any:
            raise RuntimeError("copy failed")

    observe_mod.schedule_closeout_route_probe_observe(
        request_id="r2",
        idempotency_key="k",
        source_ref="todo:x",
        closeout=_Uncopyable(schema_version=1, status="complete", x=1),
    )
    await _wait_route_probe_events(events, n=1, timeout_s=2.0)
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

    await asyncio.to_thread(
        observe_mod.schedule_closeout_route_probe_observe,
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

    loop2 = asyncio.new_event_loop()
    loop2.close()

    def _boom(_: Any) -> Any:
        raise RuntimeError("boom")

    observe_mod.run_closeout_route_probe_observe(
        request_id="r1",
        idempotency_key=None,
        source_ref="workspaces://a/b.md",
        closeout=C,
        evaluate=_boom,
        loop=loop2,
    )
    assert _route_probe_events(events) == []