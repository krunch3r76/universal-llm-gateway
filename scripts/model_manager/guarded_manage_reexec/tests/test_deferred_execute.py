"""Deferred recycle wait, re-gate, bus reply (injected fakes only)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.guarded_manage_reexec import __main__ as cli
from scripts.model_manager.guarded_manage_reexec.deferred_execute import (
    DeferredRecycleContext,
    dispatch_status_terminal,
    giw_idle_probe,
    report_deferred_crash,
    run_deferred_recycle,
    wait_for_parent_and_giw_idle,
)
from scripts.model_manager.guarded_manage_reexec.deferred_gate import (
    RECYCLE_FLEET_SERVICES,
    evaluate_r3_pass_verdict,
    fleet_child_pids,
    fleet_liveness_snapshot,
    mcp_row_matches_exception_b,
    run_recycle_step1_per_service_gate,
    stargate_row_matches_exception_c,
)
from scripts.model_manager.guarded_manage_reexec.result import GuardedReexecResult
from scripts.model_manager.guarded_manage_reexec.runner import run_guarded_reexec


def _idle_row(service: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "busy": False,
        "holder": None,
        "fleet_pid": 100,
        "determination": "idle",
    }
    row.update(extra)
    row.setdefault("service", service)
    return row


def _manage_from_rows(rows: dict[str, dict[str, Any]]):
    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del kwargs
        if method == "whoami":
            return {"pid": 9, "code_version": "deadbeef", "process_start_time": "t0"}
        if method == "fleet_liveness":
            p = params or {}
            slugs = p.get("services") or list(RECYCLE_FLEET_SERVICES)
            services_out: list[dict[str, Any]] = []
            for name in slugs:
                r = rows.get(name, _idle_row(name))
                entry: dict[str, Any] = {
                    "service": name,
                    "pid": r.get("fleet_pid", 100),
                }
                if "load_marker" in r:
                    entry["load_marker"] = r["load_marker"]
                if (ve := r.get("version_evidence")) and p.get("code_ref"):
                    entry["version_evidence"] = ve
                services_out.append(entry)
            checkout = rows.get("_checkout", {"head_sha": "abc"})
            return {"services": services_out, "checkout": checkout}
        if method == "busy_status":
            name = (params or {}).get("service", "")
            return dict(rows.get(name, _idle_row(name)))
        raise AssertionError((method, params))

    return manage_call


def _child_record(pid: Any = 100, marker: Any = None) -> dict[str, Any]:
    return {"pid": pid, "marker": marker}


def _equal_children(pid: Any = 100, marker: Any = None) -> dict[str, Any]:
    return {name: _child_record(pid, marker) for name in RECYCLE_FLEET_SERVICES}


def _proof_rows(**overrides: Any) -> dict[str, Any]:
    rows: dict[str, Any] = {
        name: _idle_row(name, fleet_pid=100) for name in RECYCLE_FLEET_SERVICES
    }
    rows["mcp"] = {
        **_idle_row("mcp", fleet_pid=100),
        "version_evidence": {
            "authoritative_for_liveness": "process_start_code_version",
        },
    }
    rows["_checkout"] = {"head_sha": "abc"}
    rows.update(overrides)
    return rows


def _verdict(
    child_before: dict[str, Any],
    child_after: dict[str, Any],
    *,
    rows: dict[str, Any] | None = None,
    result_status: str = "proof-satisfied",
    target_ref: str = "abc",
) -> tuple[str, dict[str, Any]]:
    manage = _manage_from_rows(rows if rows is not None else _proof_rows())
    return evaluate_r3_pass_verdict(
        target_ref=target_ref,
        result_status=result_status,
        child_before=child_before,
        child_after=child_after,
        manage_call=manage,
        sock_path=None,
    )


def test_dispatch_status_terminal_cases() -> None:
    assert dispatch_status_terminal({"status": "completed"})
    assert dispatch_status_terminal({"status": "failed"})
    assert dispatch_status_terminal({"status": "cancelled"})
    assert not dispatch_status_terminal({"status": "running"})
    assert not dispatch_status_terminal(None)


def test_mcp_and_stargate_exception_identity() -> None:
    mcp_b = {
        "busy": True,
        "holder": {
            "kind": "mcp_life_session",
            "subject_preview": "cdp_ask running=1",
            "dispatch_id": None,
        },
    }
    assert mcp_row_matches_exception_b(mcp_b)
    assert not mcp_row_matches_exception_b({"busy": True, "holder": {"kind": "other"}})
    st_ok = {
        "busy": True,
        "holder": None,
        "probe_error": None,
        "active_work_summary": "active_count=2",
    }
    assert stargate_row_matches_exception_c(st_ok, mcp_b=True)
    assert not stargate_row_matches_exception_c(
        {**st_ok, "active_work_summary": "active_count=2 extra"},
        mcp_b=True,
    )


def test_giw_idle_probe_probe_error_refuses() -> None:
    rows = {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": False,
        "holder": None,
        "probe_error": "ConnectTimeout",
        "determination": "undetermined",
    }
    idle, reason = giw_idle_probe(
        _manage_from_rows(rows),
        lambda: type(
            "L",
            (),
            {
                "lease_snapshot": staticmethod(
                    lambda: {"holder_dispatch_id": None, "queue_depth": 0}
                )
            },
        )(),
        sock_path=None,
    )
    assert not idle
    assert reason == "giw_idle_busy_status_undetermined"


def test_step1_giw_probe_error_refuses_at_gate() -> None:
    rows = {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": False,
        "holder": None,
        "probe_error": "ConnectTimeout",
        "determination": "undetermined",
    }
    finding = run_recycle_step1_per_service_gate(
        _manage_from_rows(rows),
        parent_dispatch_id="parent-x",
    )
    assert finding is not None
    assert finding.reason == "step1_giw_busy_status_undetermined"


def test_giw_holder_on_wait_path_never_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "out.json"
    executed: list[bool] = []

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        lambda **_k: (
            executed.append(True)
            or GuardedReexecResult(
                status="proof-satisfied",
                reason="x",
                dry_run=False,
                target_ref="abc",
                executed=True,
            )
        ),
    )
    rows = {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": False,
        "holder": {"dispatch_id": "foreign-holder"},
        "determination": "idle",
    }
    ctx = DeferredRecycleContext(
        parent_dispatch_id="d-holder",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=result_path,
        wait_timeout_s=4.0,
        poll_s=2.0,
        repo_root=tmp_path,
    )
    t = {"now": 0.0}
    code, result = run_deferred_recycle(
        ctx,
        manage_call=_manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        ledger_factory=lambda: type(
            "L",
            (),
            {
                "lease_snapshot": staticmethod(
                    lambda: {"holder_dispatch_id": None, "queue_depth": 0}
                )
            },
        )(),
        reply_fn=lambda **_k: None,
        clock=lambda: t.__setitem__("now", t["now"]) or t["now"],
        sleep_fn=lambda s: t.__setitem__("now", t["now"] + s),
    )
    assert code == 2
    assert result.reason == "parent_wait_timeout"
    assert executed == []


def test_mcp_version_evidence_requires_code_ref_on_fleet_liveness() -> None:
    rows = {name: _idle_row(name, fleet_pid=100) for name in RECYCLE_FLEET_SERVICES}
    rows["mcp"] = {
        **_idle_row("mcp"),
        "version_evidence": {
            "authoritative_for_liveness": "process_start_code_version",
        },
    }
    manage = _manage_from_rows(rows)
    without_ref = fleet_liveness_snapshot(manage, None)
    mcp_row = next(r for r in without_ref["services"] if r["service"] == "mcp")
    assert "version_evidence" not in mcp_row
    with_ref = fleet_liveness_snapshot(manage, None, code_ref="abc")
    mcp_ref = next(r for r in with_ref["services"] if r["service"] == "mcp")
    assert mcp_ref.get("version_evidence", {}).get("authoritative_for_liveness") == (
        "process_start_code_version"
    )
    child = _equal_children()
    verdict, extra = evaluate_r3_pass_verdict(
        target_ref="abc",
        result_status="proof-satisfied",
        child_before=child,
        child_after=child,
        manage_call=manage,
        sock_path=None,
    )
    assert verdict == "PASS"
    assert "process_start_code_version" in extra["probe_39439"]


def test_step1_giw_holder_non_busy_refuses() -> None:
    parent = "parent-dispatch-id"
    rows = {name: _idle_row(name) for name in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": False,
        "holder": {"dispatch_id": parent},
        "fleet_pid": 100,
    }
    finding = run_recycle_step1_per_service_gate(
        _manage_from_rows(rows),
        parent_dispatch_id=parent,
    )
    assert finding is not None
    assert finding.reason == "step1_giw_agent_bus_busy"


def test_step1_exception_b_and_c_pass() -> None:
    rows = {name: _idle_row(name) for name in RECYCLE_FLEET_SERVICES}
    rows["mcp"] = {
        "busy": True,
        "holder": {
            "kind": "mcp_life_session",
            "subject_preview": "cdp_ask running=1",
            "dispatch_id": None,
        },
    }
    rows["stargate"] = {
        "busy": True,
        "holder": None,
        "probe_error": None,
        "active_work_summary": "active_count=1",
    }
    assert (
        run_recycle_step1_per_service_gate(
            _manage_from_rows(rows),
            parent_dispatch_id="parent-x",
        )
        is None
    )


def test_step1_exception_b_mismatch_refuses() -> None:
    rows = {name: _idle_row(name) for name in RECYCLE_FLEET_SERVICES}
    rows["mcp"] = {
        "busy": True,
        "holder": {"kind": "mcp_life_session", "dispatch_id": "x"},
    }
    finding = run_recycle_step1_per_service_gate(
        _manage_from_rows(rows),
        parent_dispatch_id="parent-x",
    )
    assert finding is not None
    assert finding.reason == "step1_mcp_not_exception_b"


def test_step1_unclassified_holder_refuses() -> None:
    rows = {name: _idle_row(name) for name in RECYCLE_FLEET_SERVICES}
    rows["gateway"] = {"busy": True, "holder": {"dispatch_id": "foreign"}}
    finding = run_recycle_step1_per_service_gate(
        _manage_from_rows(rows),
        parent_dispatch_id="parent-x",
    )
    assert finding is not None
    assert finding.reason == "step1_unclassified_holder"


def test_wait_parent_timeout_900s_injected_clock() -> None:
    t = {"now": 0.0}

    def clock() -> float:
        return t["now"]

    def sleep(_s: float) -> None:
        t["now"] += 2.0

    ok, refuse = wait_for_parent_and_giw_idle(
        parent_dispatch_id="d1",
        dispatch_status_fn=lambda _d: {"status": "running"},
        giw_idle_probe_fn=lambda: (True, None),
        timeout_s=900.0,
        poll_s=2.0,
        clock=clock,
        sleep_fn=sleep,
    )
    assert not ok
    assert refuse is None
    assert t["now"] == 900.0


def _advancing_clock() -> tuple[dict[str, float], Any, Any]:
    t = {"now": 0.0}

    def clock() -> float:
        return t["now"]

    def sleep(seconds: float) -> None:
        t["now"] += seconds

    return t, clock, sleep


def test_wait_transient_undetermined_then_idle_returns_ready() -> None:
    samples = iter(
        [
            (False, "giw_idle_busy_status_undetermined"),
            (True, None),
        ]
    )
    t, clock, sleep = _advancing_clock()
    ok, refuse = wait_for_parent_and_giw_idle(
        parent_dispatch_id="d1",
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: next(samples),
        timeout_s=10.0,
        poll_s=1.0,
        clock=clock,
        sleep_fn=sleep,
    )
    assert ok
    assert refuse is None
    assert t["now"] == 1.0


def test_wait_persistent_undetermined_to_deadline_names_reason() -> None:
    t, clock, sleep = _advancing_clock()
    checks: dict[str, Any] = {}
    ok, refuse = wait_for_parent_and_giw_idle(
        parent_dispatch_id="d1",
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (False, "giw_idle_busy_status_undetermined"),
        timeout_s=4.0,
        poll_s=2.0,
        clock=clock,
        sleep_fn=sleep,
        checks=checks,
    )
    assert not ok
    assert refuse == "giw_idle_busy_status_undetermined"
    assert t["now"] == 4.0
    assert checks["giw_idle_transient_consecutive"] == 2
    assert checks["giw_idle_transient_total"] == 2


def test_wait_busy_to_deadline_times_out() -> None:
    t, clock, sleep = _advancing_clock()
    ok, refuse = wait_for_parent_and_giw_idle(
        parent_dispatch_id="d1",
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (False, None),
        timeout_s=4.0,
        poll_s=2.0,
        clock=clock,
        sleep_fn=sleep,
    )
    assert not ok
    assert refuse is None
    assert t["now"] == 4.0


def test_wait_idle_parent_live_waits() -> None:
    t, clock, sleep = _advancing_clock()
    ok, refuse = wait_for_parent_and_giw_idle(
        parent_dispatch_id="d1",
        dispatch_status_fn=lambda _d: {"status": "running"},
        giw_idle_probe_fn=lambda: (True, None),
        timeout_s=4.0,
        poll_s=2.0,
        clock=clock,
        sleep_fn=sleep,
    )
    assert not ok
    assert refuse is None
    assert t["now"] == 4.0


def test_deferred_timeout_posts_one_reply_and_writes_result(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "out.json"
    replies: list[dict[str, Any]] = []

    ctx = DeferredRecycleContext(
        parent_dispatch_id="d-timeout",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=result_path,
        wait_timeout_s=4.0,
        poll_s=2.0,
        repo_root=tmp_path,
    )
    t = {"now": 0.0}

    code, result = run_deferred_recycle(
        ctx,
        manage_call=_manage_from_rows(
            {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
        ),
        dispatch_status_fn=lambda _d: {"status": "running"},
        giw_idle_probe_fn=lambda: (True, None),
        reply_fn=lambda **kw: replies.append(kw),
        clock=lambda: t.__setitem__("now", t["now"]) or t["now"],
        sleep_fn=lambda s: t.__setitem__("now", t["now"] + s),
    )
    assert code == 2
    assert result.reason == "parent_wait_timeout"
    assert result_path.is_file()
    assert len(replies) == 1
    assert "child_pids_after: {}" in replies[0]["body"]


def test_giw_busy_during_wait_never_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "out.json"
    executed: list[bool] = []

    def _boom(**_k: Any) -> GuardedReexecResult:
        executed.append(True)
        raise AssertionError("run_guarded_reexec must not run")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        _boom,
    )
    rows = {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {"busy": True, "holder": None, "fleet_pid": 1}

    ctx = DeferredRecycleContext(
        parent_dispatch_id="d-giw-busy",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=result_path,
        wait_timeout_s=4.0,
        poll_s=2.0,
        repo_root=tmp_path,
    )
    t = {"now": 0.0}
    code, result = run_deferred_recycle(
        ctx,
        manage_call=_manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        ledger_factory=lambda: type(
            "L",
            (),
            {
                "lease_snapshot": staticmethod(
                    lambda: {"holder_dispatch_id": None, "queue_depth": 0}
                )
            },
        )(),
        reply_fn=lambda **_k: None,
        clock=lambda: t.__setitem__("now", t["now"]) or t["now"],
        sleep_fn=lambda s: t.__setitem__("now", t["now"] + s),
    )
    assert code == 2
    assert result.reason == "parent_wait_timeout"
    assert executed == []


def _recycle_ctx(
    tmp_path: Path, *, name: str, timeout_s: float, poll_s: float
) -> DeferredRecycleContext:
    return DeferredRecycleContext(
        parent_dispatch_id="d-step1",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=tmp_path / name,
        wait_timeout_s=timeout_s,
        poll_s=poll_s,
        repo_root=tmp_path,
    )


def test_step1_undetermined_reentry_bounded_by_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed: list[bool] = []

    def _boom(**_k: Any) -> GuardedReexecResult:
        executed.append(True)
        raise AssertionError("run_guarded_reexec must not run")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        _boom,
    )
    rows = {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": False,
        "holder": None,
        "probe_error": "ReadTimeout",
        "determination": "undetermined",
    }
    t, clock, sleep = _advancing_clock()
    code, result = run_deferred_recycle(
        _recycle_ctx(tmp_path, name="undetermined.json", timeout_s=5.0, poll_s=2.0),
        manage_call=_manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (True, None),
        reply_fn=lambda **_k: None,
        clock=clock,
        sleep_fn=sleep,
    )
    assert code == 2
    assert result.executed is False
    assert result.reason == "step1_giw_busy_status_undetermined"
    assert executed == []
    assert t["now"] == 5.0
    assert t["now"] <= 5.0


def test_step1_undetermined_then_idle_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _proof_rows()
    giw_calls = {"n": 0}

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        if (
            method == "busy_status"
            and (params or {}).get("service") == "git_integration_worker"
        ):
            giw_calls["n"] += 1
            if giw_calls["n"] == 1:
                return {
                    "busy": False,
                    "holder": None,
                    "probe_error": "ReadTimeout",
                    "determination": "undetermined",
                    "service": "git_integration_worker",
                }
        return _manage_from_rows(rows)(method, params, **kwargs)

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        lambda **_k: GuardedReexecResult(
            status="proof-satisfied",
            reason="proof_satisfied",
            dry_run=False,
            target_ref="abc",
            executed=True,
        ),
    )
    t, clock, sleep = _advancing_clock()
    code, result = run_deferred_recycle(
        _recycle_ctx(tmp_path, name="then-idle.json", timeout_s=10.0, poll_s=2.0),
        manage_call=manage_call,
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (True, None),
        reply_fn=lambda **_k: None,
        clock=clock,
        sleep_fn=sleep,
    )
    assert code == 0
    assert result.executed is True
    assert giw_calls["n"] >= 2
    assert t["now"] == 2.0
    assert t["now"] <= 10.0


def _step1_determination_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    determination: str,
    result_name: str,
) -> tuple[int, GuardedReexecResult, float]:
    executed: list[bool] = []

    def _boom(**_k: Any) -> GuardedReexecResult:
        executed.append(True)
        raise AssertionError("run_guarded_reexec must not run")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        _boom,
    )
    rows = {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": determination == "busy",
        "holder": None,
        "probe_error": None,
        "determination": determination,
    }
    t, clock, sleep = _advancing_clock()
    code, result = run_deferred_recycle(
        _recycle_ctx(tmp_path, name=result_name, timeout_s=10.0, poll_s=2.0),
        manage_call=_manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (True, None),
        reply_fn=lambda **_k: None,
        clock=clock,
        sleep_fn=sleep,
    )
    assert executed == []
    return code, result, t["now"]


def test_step1_giw_busy_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, result, elapsed = _step1_determination_exits(
        tmp_path,
        monkeypatch,
        determination="busy",
        result_name="busy.json",
    )
    assert code == 2
    assert result.executed is False
    assert result.reason == "step1_giw_busy_status_undetermined"
    assert elapsed == 0.0


def test_step1_giw_dead_heartbeat_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, result, elapsed = _step1_determination_exits(
        tmp_path,
        monkeypatch,
        determination="dead_heartbeat",
        result_name="dead.json",
    )
    assert code == 2
    assert result.executed is False
    assert result.reason == "step1_giw_busy_status_undetermined"
    assert elapsed == 0.0


def test_deferred_success_single_reply_and_p3_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "out.json"
    replies: list[dict[str, Any]] = []
    rows = {name: _idle_row(name, fleet_pid=100) for name in RECYCLE_FLEET_SERVICES}
    rows["_checkout"] = {"head_sha": "abc"}
    rows["mcp"] = {
        **_idle_row("mcp"),
        "version_evidence": {
            "authoritative_for_liveness": "process_start_code_version",
        },
    }

    fake_result = GuardedReexecResult(
        status="proof-satisfied",
        reason="proof_satisfied",
        dry_run=False,
        target_ref="abc",
        executed=True,
    )

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        lambda **_k: fake_result,
    )

    ctx = DeferredRecycleContext(
        parent_dispatch_id="d-done",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=result_path,
        wait_timeout_s=10.0,
        repo_root=tmp_path,
    )
    code, result = run_deferred_recycle(
        ctx,
        manage_call=_manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (True, None),
        reply_fn=lambda **kw: replies.append(kw),
        sleep_fn=lambda _s: None,
    )
    assert code == 0
    assert result.status == "proof-satisfied"
    assert len(replies) == 1
    assert "VERDICT: PASS" in replies[0]["body"]
    assert "authoritative_for_liveness" in replies[0]["body"]
    assert "39455: evidence = step 5" in replies[0]["body"]


_GATEWAY_STARTED_AT = "2026-10-17T16:00:00Z"
_GATEWAY_MARKER = {
    "kind": "container_started_at",
    "value_utc": _GATEWAY_STARTED_AT,
    "error": None,
}


def _success_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: dict[str, Any],
    replies: list[dict[str, Any]],
    *,
    result_name: str,
) -> int:
    fake_result = GuardedReexecResult(
        status="proof-satisfied",
        reason="proof_satisfied",
        dry_run=False,
        target_ref="abc",
        executed=True,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        lambda **_k: fake_result,
    )
    ctx = DeferredRecycleContext(
        parent_dispatch_id="d-done",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=tmp_path / result_name,
        wait_timeout_s=10.0,
        repo_root=tmp_path,
    )
    code, _result = run_deferred_recycle(
        ctx,
        manage_call=_manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (True, None),
        reply_fn=lambda **kw: replies.append(kw),
        sleep_fn=lambda _s: None,
    )
    return code


def test_deferred_success_reply_carries_pid_unverifiable_and_marker_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies: list[dict[str, Any]] = []
    rows = _proof_rows(
        gateway={
            **_idle_row("gateway", fleet_pid=None),
            "load_marker": _GATEWAY_MARKER,
        }
    )
    code = _success_reply(tmp_path, monkeypatch, rows, replies, result_name="out.json")
    assert code == 0
    assert len(replies) == 1
    assert "pid_unverifiable: gateway" in replies[0]["body"]
    assert (
        "marker_compare: gateway container_started_at "
        "2026-10-17T16:00:00Z -> 2026-10-17T16:00:00Z unchanged" in replies[0]["body"]
    )

    replies.clear()
    control = _proof_rows()
    code = _success_reply(
        tmp_path, monkeypatch, control, replies, result_name="control.json"
    )
    assert code == 0
    assert len(replies) == 1
    assert "pid_unverifiable" not in replies[0]["body"]


def test_none_pid_absent_marker_is_indeterminate() -> None:
    rows = _proof_rows(gateway=_idle_row("gateway", fleet_pid=None))
    manage = _manage_from_rows(rows)
    child = fleet_child_pids(manage, None)
    assert child["gateway"] == {"pid": None, "marker": None}
    verdict, extra = _verdict(child, child, rows=rows)
    assert verdict == "INDETERMINATE"
    assert "pid_unverifiable" not in extra


def test_none_pid_errored_or_wrong_kind_marker_is_indeterminate() -> None:
    errored = {
        "kind": "container_started_at",
        "value_utc": _GATEWAY_STARTED_AT,
        "error": "clock_skew",
    }
    wrong_kind = {
        "kind": "host_proc_start",
        "value_utc": _GATEWAY_STARTED_AT,
        "error": None,
    }
    for marker in (errored, wrong_kind):
        rows = _proof_rows(
            gateway={
                **_idle_row("gateway", fleet_pid=None),
                "load_marker": marker,
            }
        )
        manage = _manage_from_rows(rows)
        child = fleet_child_pids(manage, None)
        assert child["gateway"]["marker"] is None
        verdict, extra = _verdict(child, child, rows=rows)
        assert verdict == "INDETERMINATE"
        assert "pid_unverifiable" not in extra


def test_none_pid_marker_change_is_fail() -> None:
    before = _equal_children()
    after = _equal_children()
    before["gateway"] = _child_record(None, _GATEWAY_STARTED_AT)
    after["gateway"] = _child_record(None, "2026-10-17T17:00:00Z")
    verdict, _extra = _verdict(before, after)
    assert verdict == "FAIL"


def test_host_pid_change_is_fail() -> None:
    before = _equal_children()
    after = _equal_children()
    after["stargate"] = _child_record(101, None)
    verdict, _extra = _verdict(before, after)
    assert verdict == "FAIL"


def test_none_pid_on_one_side_is_fail() -> None:
    before = _equal_children()
    after = _equal_children()
    before["gateway"] = _child_record(None, _GATEWAY_STARTED_AT)
    verdict, _extra = _verdict(before, after)
    assert verdict == "FAIL"


def test_identical_non_null_pids_pass_without_pid_unverifiable() -> None:
    child = _equal_children()
    verdict, extra = _verdict(child, child)
    assert verdict == "PASS"
    assert "pid_unverifiable" not in extra
    assert "marker_compare" not in extra


def test_none_pid_equal_markers_pass_and_record_unverifiable() -> None:
    child = _equal_children()
    child["gateway"] = _child_record(None, _GATEWAY_STARTED_AT)
    verdict, extra = _verdict(child, child)
    assert verdict == "PASS"
    assert extra["pid_unverifiable"] == ["gateway"]
    assert extra["marker_compare"] == [
        "marker_compare: gateway container_started_at "
        "2026-10-17T16:00:00Z -> 2026-10-17T16:00:00Z unchanged"
    ]


def test_service_fail_beats_indeterminate_and_head_sha_still_fails() -> None:
    before = _equal_children()
    after = _equal_children()
    before["gateway"] = _child_record(None, None)
    after["rag"] = _child_record(101, None)
    verdict, _extra = _verdict(before, after)
    assert verdict == "FAIL"

    child = _equal_children()
    child["gateway"] = _child_record(None, None)
    verdict, _extra = _verdict(child, child, target_ref="not-the-head")
    assert verdict == "FAIL"


def test_report_deferred_crash_one_reply_and_result_file(tmp_path: Path) -> None:
    result_path = tmp_path / "crash.json"
    replies: list[dict[str, Any]] = []
    ctx = DeferredRecycleContext(
        parent_dispatch_id="d-crash",
        report_thread="15851",
        report_after_turn=1293,
        target_ref="abc",
        tmux_target="0:0",
        result_path=result_path,
        repo_root=tmp_path,
    )
    report_deferred_crash(
        ctx,
        RuntimeError("ledger boom"),
        "traceback-here",
        reply_fn=lambda **kw: replies.append(kw),
    )
    assert len(replies) == 1
    on_disk = json.loads(result_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "crashed"
    assert on_disk["reason"] == "ledger boom"


def test_main_deferred_crash_on_dispatch_status_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies: list[dict[str, Any]] = []
    result_path = tmp_path / "out.json"
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.run_deferred_recycle",
        lambda _ctx: (_ for _ in ()).throw(RuntimeError("status read failed")),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.report_deferred_crash",
        lambda ctx, exc, tb, **kw: report_deferred_crash(
            ctx, exc, tb, reply_fn=lambda **r: replies.append(r)
        ),
    )
    with pytest.raises(RuntimeError, match="status read failed"):
        cli.main(
            [
                "--execute",
                "--recover",
                "--target-ref",
                "abc",
                "--parent-dispatch-id",
                "d1",
                "--report-thread",
                "15851",
                "--report-after-turn",
                "1293",
                "--result-path",
                str(result_path),
            ]
        )
    assert len(replies) == 1
    assert result_path.is_file()


def test_in_dispatch_execute_still_refuses_giw_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "caller-dispatch")
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {
                "process": {"manage_inflight": 1, "activities": []},
                "services": {
                    "git_integration_worker": {
                        "active_work": {"active_count": 1},
                    }
                },
            }
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
    )
    assert result.status == "refused"
    assert result.reason == "giw_claimed_occupants"


def test_cli_deferred_requires_report_fields() -> None:
    code = cli.main(
        [
            "--execute",
            "--recover",
            "--target-ref",
            "abc",
            "--parent-dispatch-id",
            "d1",
        ]
    )
    assert code == 2
