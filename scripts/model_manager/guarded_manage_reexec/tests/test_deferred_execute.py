"""Deferred recycle wait, re-gate, bus reply (injected fakes only)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.guarded_manage_reexec import __main__ as cli
from scripts.model_manager.guarded_manage_reexec.deferred_execute import (
    RECYCLE_FLEET_SERVICES,
    DeferredRecycleContext,
    dispatch_status_terminal,
    mcp_row_matches_exception_b,
    run_deferred_recycle,
    run_recycle_step1_per_service_gate,
    stargate_row_matches_exception_c,
    wait_for_parent_and_giw_idle,
)
from scripts.model_manager.guarded_manage_reexec.result import GuardedReexecResult
from scripts.model_manager.guarded_manage_reexec.runner import run_guarded_reexec


def _idle_row(service: str, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"busy": False, "holder": None}
    row.update(extra)
    row.setdefault("service", service)
    return row


def _manage_from_rows(rows: dict[str, dict[str, Any]]):
    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del kwargs
        if method == "whoami":
            return {"pid": 9, "code_version": "deadbeef", "process_start_time": "t0"}
        if method == "health":
            name = (params or {}).get("service", "")
            return {"pid": rows.get(name, {}).get("health_pid", 100)}
        if method == "busy_status":
            name = (params or {}).get("service", "")
            return dict(rows.get(name, _idle_row(name)))
        raise AssertionError((method, params))

    return manage_call


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


def test_step1_exception_a_parent_holder_still_refuses() -> None:
    parent = "parent-dispatch-id"
    rows = {name: _idle_row(name) for name in RECYCLE_FLEET_SERVICES}
    rows["git_integration_worker"] = {
        "busy": True,
        "holder": {"dispatch_id": parent},
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

    statuses = iter(
        [
            {"status": "running"},
            {"status": "running"},
        ]
    )

    def status_fn(_dispatch_id: str) -> dict[str, Any]:
        try:
            return next(statuses)
        except StopIteration:
            return {"status": "running"}

    ok = wait_for_parent_and_giw_idle(
        parent_dispatch_id="d1",
        dispatch_status_fn=status_fn,
        giw_idle_fn=lambda: True,
        timeout_s=900.0,
        poll_s=2.0,
        clock=clock,
        sleep_fn=sleep,
    )
    assert not ok
    assert t["now"] == 900.0


def test_deferred_timeout_posts_one_reply_and_writes_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "out.json"
    replies: list[dict[str, Any]] = []

    def reply_fn(**kwargs: Any) -> None:
        replies.append(kwargs)

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

    def clock() -> float:
        return t["now"]

    def sleep_fn(seconds: float) -> None:
        t["now"] += seconds

    code, result = run_deferred_recycle(
        ctx,
        manage_call=_manage_from_rows(
            {n: _idle_row(n) for n in RECYCLE_FLEET_SERVICES}
        ),
        dispatch_status_fn=lambda _d: {"status": "running"},
        giw_idle_fn=lambda: True,
        reply_fn=reply_fn,
        clock=clock,
        sleep_fn=sleep_fn,
    )
    assert code == 2
    assert result.reason == "parent_wait_timeout"
    assert result_path.is_file()
    on_disk = json.loads(result_path.read_text(encoding="utf-8"))
    assert on_disk["reason"] == "parent_wait_timeout"
    assert len(replies) == 1
    assert replies[0]["thread"] == "15851"
    assert replies[0]["to"] == "maestro"
    assert replies[0]["after_turn"] == 1293
    assert str(result_path) in replies[0]["body"]


def test_deferred_success_single_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "out.json"
    replies: list[dict[str, Any]] = []
    rows = {name: _idle_row(name) for name in RECYCLE_FLEET_SERVICES}

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
        giw_idle_fn=lambda: True,
        reply_fn=lambda **kw: replies.append(kw),
        sleep_fn=lambda _s: None,
    )
    assert code == 0
    assert result.status == "proof-satisfied"
    assert len(replies) == 1
    assert "VERDICT: PASS" in replies[0]["body"]


def test_in_dispatch_execute_still_refuses_giw_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """r2 regression: inline --execute with CURSOR_SDK_DISPATCH_ID still refuses."""
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
