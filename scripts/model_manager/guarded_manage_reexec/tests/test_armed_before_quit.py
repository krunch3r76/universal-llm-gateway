"""Runner witnesses: arm before quit, intent recheck, teardown on refuse."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from libs.manage_handover import remove_armed_record, write_armed_record
from scripts.model_manager.guarded_manage_reexec.runner import run_guarded_reexec
from scripts.model_manager.ui.controller.restart_intent_store import (
    RestartIntentStore,
)


def _store(tmp_path: Path) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


@pytest.mark.offline
def test_open_intent_after_arm_refuses_before_quit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_path = tmp_path / "manage.armed.json"
    tmux_log: list[list[str]] = []
    killed_pids: list[int] = []
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
        tmux_log.append(cmd)
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            write_armed_record(
                record_path,
                pid=os.getpid(),
                code_version="deadbeef",
                process_start_time="2026-08-11T00:00:00+00:00",
            )
            store.create_intent(
                service="git_integration_worker",
                action="sync_restart",
                deadline_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                reason="post-arm hook",
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="%42\n", stderr="")
        if cmd[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    def kill_pid(pid: int) -> None:
        killed_pids.append(pid)

    before_pid = manage_call("whoami")["pid"]
    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001
        armed_record_path=record_path,
        run_cmd=run_cmd,
        kill_pid_fn=kill_pid,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        boot_timeout_s=1.0,
        quit_timeout_s=0.2,
    )
    assert result.status == "refused"
    assert "nonterminal_restart_intent" in result.reason
    assert result.executed is False
    assert not any(
        c[:2] == ["tmux", "send-keys"] and len(c) > 4 and c[4] == "q"
        for c in tmux_log
    )
    assert any(c[:2] == ["tmux", "kill-pane"] for c in tmux_log)
    assert os.getpid() in killed_pids
    assert read_armed_record_missing(record_path)
    assert manage_call("whoami")["pid"] == before_pid


def read_armed_record_missing(path: Path) -> bool:
    remove_armed_record(path)
    from libs.manage_handover import read_armed_record

    return read_armed_record(path) is None


@pytest.mark.offline
def test_start_not_proven_refuses_without_quit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record_path = tmp_path / "manage.armed.json"
    tmux_log: list[list[str]] = []

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
        tmux_log.append(cmd)
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="%99\n", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001
        armed_record_path=record_path,
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        boot_timeout_s=0.3,
        quit_timeout_s=0.2,
    )
    assert result.status == "refused"
    assert result.reason == "successor_not_armed"
    assert result.executed is False
    assert not any(
        c[:2] == ["tmux", "send-keys"] and len(c) > 4 and c[4] == "q"
        for c in tmux_log
    )
    assert result.reason != "checks_passed_stopped_before_quit"


@pytest.mark.offline
def test_recovery_path_import_removed() -> None:
    with pytest.raises(ImportError):
        from scripts.model_manager.guarded_manage_reexec.result import (  # noqa: F401
            RECOVERY_PATH,
        )
