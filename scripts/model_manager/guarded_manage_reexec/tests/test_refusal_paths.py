"""Refusal-path unit tests for guarded manage reexec (no live manage).

AC: non-terminal intent, manage_inflight others, drain not clear, pane-pid
mismatch, never-healthy start — each asserts refusal/failure, not merely
happy-path coverage.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.guarded_manage_reexec.checks import (
    collect_refuse_report,
    observe_drain_clear,
    observe_manage_inflight,
    observe_nonterminal_intents,
)
from scripts.model_manager.guarded_manage_reexec.pane import (
    observe_tmux_pane_hosts_manage,
)
from scripts.model_manager.guarded_manage_reexec.runner import (
    RECOVERY_PATH,
    prove_pickup,
    run_guarded_reexec,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)


def _ok_tmux(pane_pid: int = 9):
    """Return a run_cmd that reports a matching pane_pid for display-message."""

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"{pane_pid}\n", stderr=""
            )
        raise AssertionError(f"unexpected cmd: {cmd}")

    return run_cmd


def _store(tmp_path: Any) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


def test_refuse_dispatch_home_before_any_manage_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overlay HOME ⇒ refuse before whoami/quit (M2 / AC1)."""
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    overlay = dispatch_root / "auto-refuse-home"
    overlay.mkdir(parents=True)
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    monkeypatch.setenv("HOME", str(overlay))

    calls: list[str] = []

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        calls.append(method)
        raise AssertionError("must not call manage under dispatch HOME")

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=True,
        manage_call=manage_call,
    )
    assert result.status == "dry-run"
    assert result.reason == "dispatch_home_host_refusal"
    assert result.executed is False
    assert calls == []


def test_refuse_nonterminal_restart_intent(tmp_path: Any) -> None:
    """Non-terminal intent present ⇒ observe_nonterminal_intents refuses."""
    store = _store(tmp_path)
    intent = store.create_intent(
        service="git_integration_worker",
        action="sync_restart",
        deadline_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        reason="fixture",
    )
    assert intent.status == STATUS_PENDING_DRAIN
    finding = observe_nonterminal_intents(store)
    assert finding is not None
    assert finding.reason == "nonterminal_restart_intent"
    assert finding.offenders[0]["intent_id"] == intent.intent_id
    assert finding.offenders[0]["status"] == STATUS_PENDING_DRAIN

    report = collect_refuse_report(
        busy_status_fn=lambda: {
            "process": {"manage_inflight": 1, "activities": []},
            "charter_hold": {"held": True, "pause_drain_clear": True},
        },
        store=store,
        require_drain_clear=True,
    )
    assert report.refused is True
    assert any(f.reason == "nonterminal_restart_intent" for f in report.findings)


def test_refuse_manage_inflight_others() -> None:
    """manage_inflight raw>1 (others beyond busy_status self) ⇒ refuse."""
    finding, raw, others, activities = observe_manage_inflight(
        {"process": {"manage_inflight": 2, "activities": []}}
    )
    assert finding is not None
    assert finding.reason == "manage_inflight_or_activities"
    assert raw == 2
    assert others == 1
    assert activities == []

    # Self-only observation (raw==1) must NOT refuse — decorative >0 avoided.
    finding_self, raw_self, others_self, _ = observe_manage_inflight(
        {"process": {"manage_inflight": 1, "activities": []}}
    )
    assert finding_self is None
    assert raw_self == 1
    assert others_self == 0


def test_refuse_manage_inflight_others_via_report(tmp_path: Any) -> None:
    """collect_refuse_report refuses when manage_inflight others > 0."""
    store = _store(tmp_path)
    report = collect_refuse_report(
        busy_status_fn=lambda: {
            "process": {"manage_inflight": 2, "activities": []},
            "charter_hold": {"held": True, "pause_drain_clear": True},
        },
        store=store,
        require_drain_clear=True,
    )
    assert report.refused is True
    assert report.manage_inflight_raw == 2
    assert report.manage_inflight_others == 1
    assert any(f.reason == "manage_inflight_or_activities" for f in report.findings)


def test_refuse_drain_not_clear() -> None:
    """pause_drain_clear false/absent ⇒ refuse (wire field, not safe_to_quit)."""
    finding = observe_drain_clear(
        {
            "held": False,
            "pause_drain_clear": False,
            "tick_in_flight": False,
            "live_charter_shaped_dispatches": [],
        }
    )
    assert finding is not None
    assert finding.reason == "drain_not_clear"
    assert finding.offenders[0]["pause_drain_clear"] is False
    assert finding.offenders[0]["safe_to_quit_on_wire"] is None

    finding_ok = observe_drain_clear(
        {"held": True, "pause_drain_clear": True, "tick_in_flight": False}
    )
    assert finding_ok is None


def test_refuse_drain_not_clear_via_report(tmp_path: Any) -> None:
    """collect_refuse_report refuses when pause_drain_clear is not true."""
    store = _store(tmp_path)
    report = collect_refuse_report(
        busy_status_fn=lambda: {
            "process": {"manage_inflight": 1, "activities": []},
            "charter_hold": {"held": False, "pause_drain_clear": False},
        },
        hold_status_fn=lambda: {
            "held": False,
            "pause_drain_clear": False,
            "tick_in_flight": False,
            "live_charter_shaped_dispatches": [],
        },
        store=store,
        require_drain_clear=True,
    )
    assert report.refused is True
    assert any(f.reason == "drain_not_clear" for f in report.findings)


def test_prove_pickup_rejects_pid_only() -> None:
    """New pid alone is not proof — start_time and code_version both required."""
    before = {
        "pid": 1,
        "code_version": "aaa",
        "process_start_time": "2026-08-10T00:00:00+00:00",
    }
    after_same_start = {
        "pid": 2,
        "code_version": "bbb",
        "process_start_time": "2026-08-10T00:00:00+00:00",
    }
    v_ok, s_ok, reason = prove_pickup(
        before=before, after=after_same_start, target_ref="bbb"
    )
    assert v_ok is True
    assert s_ok is False
    assert "process_start_time_not_later" in reason

    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    after_ok = {
        "pid": 2,
        "code_version": "bbb",
        "process_start_time": later,
    }
    v_ok2, s_ok2, reason2 = prove_pickup(
        before=before, after=after_ok, target_ref="bbb"
    )
    assert v_ok2 is True and s_ok2 is True
    assert reason2 == "proof_satisfied"


def test_dry_run_stops_before_quit(tmp_path: Any) -> None:
    """Dry-run with clear checks reports stopped_before_quit and executed=False."""
    store = _store(tmp_path)
    calls: list[str] = []

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        calls.append(method)
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {
                "process": {"manage_inflight": 1, "activities": []},
                "restart_windows": {"open": []},
                "charter_hold": {"held": True, "pause_drain_clear": True},
            }
        if method == "charter_hold_status":
            return {
                "held": True,
                "pause_drain_clear": True,
                "tick_in_flight": False,
                "live_charter_shaped_dispatches": [],
            }
        raise AssertionError(f"unexpected manage call in dry-run: {method}")

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=True,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001 — test injection
        run_cmd=_ok_tmux(9),
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
    )
    assert result.status == "dry-run"
    assert result.reason == "checks_passed_stopped_before_quit"
    assert result.executed is False
    assert result.recovery_path == RECOVERY_PATH
    assert result.recovery_path == "manual, human at the terminal"
    assert "charter_pause" not in calls


def test_refuse_tmux_pane_pid_mismatch() -> None:
    """Target pane that does not host live manage pid ⇒ refuse before quit."""
    finding = observe_tmux_pane_hosts_manage(
        tmux_target="0:0",
        manage_pid=42,
        run_cmd=_ok_tmux(99),
        tree_contains_fn=lambda pid, ancestor: False,
    )
    assert finding is not None
    assert finding.reason == "tmux_pane_pid_mismatch"
    assert finding.offenders[0]["pane_pid"] == 99
    assert finding.offenders[0]["manage_pid"] == 42


def test_run_refuses_when_tmux_pane_mismatches(tmp_path: Any) -> None:
    """run_guarded_reexec refuses (executed=False) when pane does not match pid."""
    store = _store(tmp_path)

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            return {
                "pid": 42,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {
                "process": {"manage_inflight": 1, "activities": []},
                "charter_hold": {"held": True, "pause_drain_clear": True},
            }
        if method == "charter_hold_status":
            return {
                "held": True,
                "pause_drain_clear": True,
                "tick_in_flight": False,
                "live_charter_shaped_dispatches": [],
            }
        raise AssertionError(method)

    sends: list[list[str]] = []

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="99\n", stderr="")
        if cmd[:2] == ["tmux", "send-keys"]:
            sends.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: False,
    )
    assert result.status == "refused"
    assert "tmux_pane_pid_mismatch" in result.reason
    assert result.executed is False
    assert result.whoami_before is not None
    assert result.whoami_before["pid"] == 42
    assert sends == []


def test_start_never_healthy_reports_failure_not_hang(tmp_path: Any) -> None:
    """Quit-ok + sock never-up ⇒ status=start-failed within boot_timeout bound."""
    store = _store(tmp_path)
    state = {"down": False}

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            if state["down"]:
                return {"status": "error", "reason": "manage_sock_missing"}
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {
                "process": {"manage_inflight": 1, "activities": []},
                "charter_hold": {"held": True, "pause_drain_clear": True},
            }
        if method == "charter_hold_status":
            return {
                "held": True,
                "pause_drain_clear": True,
                "tick_in_flight": False,
                "live_charter_shaped_dispatches": [],
            }
        if method == "charter_pause":
            return {"status": "ok", "held": True}
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "send-keys"]:
            # First send is quit (`q`); subsequent is start — stay down either way.
            state["down"] = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    t0 = time.monotonic()
    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        quit_timeout_s=0.2,
        boot_timeout_s=0.2,
    )
    elapsed = time.monotonic() - t0
    assert result.status == "start-failed"
    assert result.reason == "reexec_sock_not_up"
    assert result.executed is True
    assert result.whoami_before is not None
    assert result.whoami_before["pid"] == 9
    assert result.boot_timeout_s == 0.2
    assert result.recovery_path == "manual, human at the terminal"
    # Must terminate: wall clock well under an unbounded hang (≪ 30s).
    assert elapsed < 5.0
    # Opposite of precondition refuse: status is not "refused".
    assert result.status != "refused"
