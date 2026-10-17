"""Unit tests for detach launcher and recover execute path (injected fakes only)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import scripts.model_manager.guarded_manage_reexec as guarded_pkg
from libs.manage_handover import write_armed_record
from scripts.model_manager.guarded_manage_reexec import __main__ as cli
from scripts.model_manager.guarded_manage_reexec.checks import RefuseFinding
from scripts.model_manager.guarded_manage_reexec.detach import (
    LOG_PATH,
    build_child_env,
    build_systemd_run_cmd,
    child_execute_argv,
    launch_detach,
    parse_active_reexec_units,
    read_env_local_subset,
)
from scripts.model_manager.guarded_manage_reexec.runner import (
    DEFAULT_TMUX_TARGET,
    run_guarded_reexec,
)
from scripts.model_manager.guarded_manage_reexec.seat_prelude import InflightWaitResult
from scripts.model_manager.ui import app as ui_app


def _ok_tmux(pane_pid: int = 9):
    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"{pane_pid}\n", stderr=""
            )
        raise AssertionError(f"unexpected cmd: {cmd}")

    return run_cmd


def test_detach_launch_builds_transient_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 1 / AC1-2: systemd-run service shape, child env, XDG when unset."""
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".env.local").write_text(
        "MANAGE_SOCKET=/tmp/fake-manage.sock\nGATEWAY_DIR=/tmp/gw\nSECRET=drop\n",
        encoding="utf-8",
    )
    result_path = hub / "tmp" / "result.json"
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    def run_cmd(
        cmd: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, env))
        if cmd[:3] == ["systemctl", "--user", "list-units"]:
            assert env is not None
            uid = os.getuid()
            assert env["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"
            assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{uid}/bus"
            assert "--plain" in cmd
            assert "--state=active,activating" in cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "systemd-run":
            assert env is not None
            uid = os.getuid()
            assert env["XDG_RUNTIME_DIR"] == f"/run/user/{uid}"
            assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{uid}/bus"
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.detach.unit_name_for_launch",
        lambda: "ulg-manage-reexec-20260101T000000Z.service",
    )

    code, payload = launch_detach(
        target_ref="abc123",
        tmux_target=DEFAULT_TMUX_TARGET,
        hub=hub,
        result_path=result_path,
        run_cmd=run_cmd,
        python_bin="/abs/python",
    )
    assert code == 0
    assert payload["status"] == "launched"
    assert payload["result_path"] == str(result_path)

    systemd_calls = [c for c, _ in calls if c and c[0] == "systemd-run"]
    assert len(systemd_calls) == 1
    cmd = systemd_calls[0]
    assert "--collect" in cmd
    unit_args = [part for part in cmd if part.startswith("--unit=")]
    assert unit_args == ["--unit=ulg-manage-reexec-20260101T000000Z.service"]
    assert "-p" in cmd and "KillMode=process" in cmd
    assert f"StandardOutput=append:{LOG_PATH}" in cmd
    assert f"--working-directory={hub}" in cmd
    assert "--setenv" in cmd
    env_blob = " ".join(cmd)
    assert "HOME=" in env_blob
    assert f"PROJECT_ROOT={hub}" in env_blob
    assert f"PYTHONPATH={hub}/libs:{hub}/services/universal-stargate" in env_blob
    assert "MANAGE_SOCKET=/tmp/fake-manage.sock" in env_blob
    assert "GATEWAY_DIR=/tmp/gw" in env_blob
    assert "CURSOR_SDK_DISPATCH_ID" not in env_blob
    assert "/abs/python" in cmd
    assert "--execute" in cmd and "--recover" in cmd
    assert str(result_path) in cmd


def test_detach_refuses_active_unit(tmp_path: Path) -> None:
    """Test 2 / AC3: active list-units row blocks second launch."""
    calls: list[list[str]] = []

    def run_cmd(
        cmd: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del env
        calls.append(cmd)
        if cmd[:3] == ["systemctl", "--user", "list-units"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    "ulg-manage-reexec-20260101T000000Z.service loaded active running "
                    "Guarded manage reexec\n"
                ),
                stderr="",
            )
        raise AssertionError("systemd-run must not run")

    code, payload = launch_detach(
        target_ref="abc",
        tmux_target=DEFAULT_TMUX_TARGET,
        hub=tmp_path,
        result_path=tmp_path / "r.json",
        run_cmd=run_cmd,
    )
    assert code == 2
    assert payload["reason"] == "reexec_unit_already_active"
    assert not any(c and c[0] == "systemd-run" for c in calls)


def test_recover_waits_launcher_write_lease_before_paired_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: recover waits launcher write lease clear before paired stop."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    busy_polls = {"n": 0}
    paired_stop_at_poll: list[int] = []
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.time.sleep",
        lambda _s: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.resolve_manage_inflight_for_seat",
        lambda *_a, **_k: InflightWaitResult(
            cleared=True, waited_s=0.0, giw_paired_required=True
        ),
    )

    def _paired_stop(*_a, **_k):
        paired_stop_at_poll.append(busy_polls["n"])
        return {"status": "ok"}

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_stop",
        _paired_stop,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_start",
        lambda *_a, **_k: {"status": "ok"},
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            busy_polls["n"] += 1
            if busy_polls["n"] <= 2:
                return {
                    "process": {"manage_inflight": 1, "activities": []},
                    "services": {
                        "git_integration_worker": {
                            "active_work": {
                                "write_lease": {
                                    "holder_dispatch_id": "d-launcher",
                                },
                            },
                        },
                    },
                }
            return {"process": {"manage_inflight": 1, "activities": []}}
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fail")
        return _ok_tmux(9)(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
    )
    assert busy_polls["n"] >= 3
    assert len(paired_stop_at_poll) == 1
    assert paired_stop_at_poll[0] > 2
    assert result.status == "refused"
    assert result.reason == "successor_spawn_failed"


def test_recover_waits_busy_occupant_then_reaches_paired_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover path waits launcher occupant off busy_status before refuse checks."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    busy_polls = {"n": 0}
    paired_stop_called: list[bool] = []
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.time.sleep",
        lambda _s: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.resolve_manage_inflight_for_seat",
        lambda *_a, **_k: InflightWaitResult(
            cleared=True, waited_s=0.0, giw_paired_required=True
        ),
    )

    def _paired_stop(*_a, **_k):
        paired_stop_called.append(True)
        return {"status": "ok"}

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_stop",
        _paired_stop,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_start",
        lambda *_a, **_k: {"status": "ok"},
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            busy_polls["n"] += 1
            if busy_polls["n"] <= 2:
                return {
                    "process": {"manage_inflight": 1, "activities": []},
                    "services": {
                        "git_integration_worker": {
                            "active_work": {"active_count": 1},
                        }
                    },
                }
            return {"process": {"manage_inflight": 1, "activities": []}}
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fail")
        return _ok_tmux(9)(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
    )
    assert busy_polls["n"] >= 3
    assert paired_stop_called == [True]
    assert result.status == "refused"
    assert result.reason == "successor_spawn_failed"


def test_occupant_poll_timeout_refuses_without_quit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 3 / AC4: 600s occupant wait timeout, no stop, no q."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    sends: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (False, "giw_claimed_occupants_timeout"),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        if method == "stop":
            sends.append(["stop"])
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "send-keys"]:
            sends.append(cmd)
        return _ok_tmux(9)(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
    )
    assert result.status == "refused"
    assert result.reason == "giw_claimed_occupants_timeout"
    assert result.executed is False
    assert sends == []


def test_stage_a_restarts_giw_when_spawn_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 4 / AC5: paired stop ok, spawn fails => GIW start, no q."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    manage_calls: list[str] = []
    tmux_cmds: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.resolve_manage_inflight_for_seat",
        lambda *_a, **_k: InflightWaitResult(
            cleared=True, waited_s=0.0, giw_paired_required=True
        ),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_stop",
        lambda *_a, **_k: {"status": "ok"},
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del kwargs
        manage_calls.append(method)
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        if method == "stop" and params == {
            "service": "git_integration_worker",
            "park_live": True,
        }:
            return {"status": "ok"}
        if method == "start" and params == {"service": "git_integration_worker"}:
            return {"status": "ok"}
        raise AssertionError((method, params))

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        tmux_cmds.append(cmd)
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fail")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
    )
    assert result.status == "refused"
    assert result.reason == "successor_spawn_failed"
    assert result.recovery == "giw_restarted_before_quit"
    assert "start" in manage_calls
    assert not any(c[:2] == ["tmux", "send-keys"] for c in tmux_cmds)


def test_stage_b_relaunch_after_successor_bind_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 5 / AC6: teardown, old pid wait, new-window manage, GIW after whoami."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    state = {"down": False, "up_after_relaunch": False}
    tmux_cmds: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_proc_exit",
        lambda _pid, _t: True,
    )
    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del kwargs
        if method == "whoami":
            if state["up_after_relaunch"]:
                return {
                    "pid": 11,
                    "code_version": "deadbeef",
                    "process_start_time": later,
                }
            if state["down"]:
                return {"status": "error", "reason": "missing"}
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        if method == "start":
            return {"status": "ok"}
        raise AssertionError((method, params))

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        tmux_cmds.append(cmd)
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            write_armed_record(
                record_path,
                pid=os.getpid(),
                code_version="deadbeef",
                process_start_time=later,
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="%1\n", stderr="")
        if cmd[:2] == ["tmux", "send-keys"] and len(cmd) > 4 and cmd[4] == "q":
            state["down"] = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["/usr/bin/tmux", "new-window", "-d"]:
            state["up_after_relaunch"] = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        kill_pid_fn=lambda _pid: None,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
        quit_timeout_s=0.2,
        boot_timeout_s=0.5,
    )
    assert result.status == "proof-failed"
    assert result.recovery == "manage_relaunched"
    assert result.old_pid_exited is True
    assert any(c[:3] == ["/usr/bin/tmux", "new-window", "-d"] for c in tmux_cmds)
    assert not any(
        c[:2] == ["tmux", "send-keys"] and len(c) > 4 and "./manage" in c
        for c in tmux_cmds
    )


def test_stage_c_quit_sock_still_up_no_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 6 / AC7: quit_sock_still_up exits 1, recovery none, no new-window."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    tmux_cmds: list[list[str]] = []
    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        tmux_cmds.append(cmd)
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            write_armed_record(
                record_path,
                pid=os.getpid(),
                code_version="deadbeef",
                process_start_time=later,
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="%1\n", stderr="")
        if cmd[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        kill_pid_fn=lambda _pid: None,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
        quit_timeout_s=0.2,
        boot_timeout_s=0.5,
    )
    assert result.status == "quit"
    assert result.recovery == "none"
    assert not any(c[:3] == ["/usr/bin/tmux", "new-window", "-d"] for c in tmux_cmds)


def test_no_charter_calls_on_detach_or_foreground_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 7 / AC8: neither recover nor foreground execute calls charter_*."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    calls: list[str] = []
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        calls.append(method)
        if method.startswith("charter"):
            raise AssertionError(method)
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        raise AssertionError(method)

    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            write_armed_record(
                record_path,
                pid=os.getpid(),
                code_version="deadbeef",
                process_start_time=later,
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="%1\n", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
        boot_timeout_s=0.5,
        quit_timeout_s=0.2,
    )
    assert "charter_pause" not in calls
    assert "charter_resume" not in calls

    calls.clear()
    run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=False,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
        boot_timeout_s=0.5,
        quit_timeout_s=0.2,
    )
    assert "charter_pause" not in calls
    assert "charter_resume" not in calls


def test_recover_pre_q_recheck_triggers_stage_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 8 / AC9: pre-q occupant hit => no q; stage (a) when stop ran."""
    record_path = tmp_path / "armed.json"
    sock = str(tmp_path / "manage.sock")
    sends: list[list[str]] = []
    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del kwargs
        if method == "whoami":
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        if method == "stop":
            return {"status": "ok"}
        if method == "start":
            return {"status": "ok"}
        raise AssertionError((method, params))

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "send-keys"]:
            sends.append(cmd)
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            write_armed_record(
                record_path,
                pid=os.getpid(),
                code_version="deadbeef",
                process_start_time=later,
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="%1\n", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_stop",
        lambda *_a, **_k: {"status": "ok"},
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.resolve_manage_inflight_for_seat",
        lambda *_a, **_k: InflightWaitResult(
            cleared=True, waited_s=0.0, giw_paired_required=True
        ),
    )

    observe_calls = {"n": 0}

    def observe(  # noqa: ANN001
        busy: dict[str, Any],
        *,
        caller_dispatch: str | None,
        dry_run: bool = False,
    ):
        del busy, dry_run
        observe_calls["n"] += 1
        if observe_calls["n"] == 2 and caller_dispatch is None:
            return RefuseFinding(
                reason="giw_claimed_occupants",
                offenders=[{"occupant": "busy_status"}],
            )
        return None

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.observe_giw_occupants",
        observe,
    )

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        kill_pid_fn=lambda _pid: None,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
    )
    assert result.status == "refused"
    assert result.reason == "giw_claimed_occupants"
    assert result.recovery == "giw_restarted_before_quit"
    assert not any(len(c) > 4 and c[4] == "q" for c in sends)


def test_detach_refuses_incomplete_deferred_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run_cmd(
        cmd: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del env
        calls.append(cmd)
        raise AssertionError("systemd-run must not run")

    import scripts.model_manager.guarded_manage_reexec.detach as detach_mod

    real_launch = detach_mod.launch_detach

    def launch_with_run_cmd(**kwargs: object) -> tuple[int, dict[str, object]]:
        kwargs["run_cmd"] = run_cmd
        return real_launch(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.launch_detach",
        launch_with_run_cmd,
    )

    code = cli.main(
        [
            "--detach",
            "--target-ref",
            "abc",
            "--parent-dispatch-id",
            "",
            "--report-thread",
            "15851",
            "--report-after-turn",
            "1293",
        ]
    )
    assert code == 2
    assert not any(c and c[0] == "systemd-run" for c in calls)


def test_detach_and_execute_mutually_exclusive() -> None:
    """Test 9 / AC10: --detach --execute exits 2 before subprocess."""
    code = cli.main(["--detach", "--execute", "--target-ref", "abc"])
    assert code == 2


def test_crashed_writes_result_after_quit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 10 / AC11: exception after q => crashed result file."""
    record_path = tmp_path / "armed.json"
    result_path = tmp_path / "out.json"
    sock = str(tmp_path / "manage.sock")
    state = {"down": False}
    later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._wait_detached_occupants_clear",
        lambda *_a, **_k: (True, ""),
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._publish_reexec",
        lambda _e: None,
    )

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "whoami":
            if state["down"]:
                return {"status": "error", "reason": "missing"}
            return {
                "pid": 9,
                "code_version": "deadbeef",
                "process_start_time": "2026-08-10T00:00:00+00:00",
            }
        if method == "busy_status":
            return {"process": {"manage_inflight": 1, "activities": []}}
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            write_armed_record(
                record_path,
                pid=os.getpid(),
                code_version="deadbeef",
                process_start_time=later,
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="%1\n", stderr="")
        raise AssertionError(cmd)

    def boom_send(*_a, **_k):
        state["down"] = True
        raise RuntimeError("post-q boom")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner._tmux_send",
        boom_send,
    )

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        recover=True,
        manage_call=manage_call,
        run_cmd=run_cmd,
        armed_record_path=record_path,
        sock_path=sock,
        result_path=result_path,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
        intent_db=tmp_path / "intents.db",
        quit_timeout_s=0.2,
    )
    assert result.status == "crashed"
    assert result.stage == "quit_sent"
    assert result.traceback
    on_disk = json.loads(result_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "crashed"
    assert on_disk["stage"] == "quit_sent"


def test_doc_claims() -> None:
    """Test 11 / AC12: package doc and app retry doc omit stale charter claims."""
    source = Path(guarded_pkg.__file__).read_text(encoding="utf-8")
    assert "handler_propagation" not in source
    doc = ui_app.ModelManagerApp._retry_api_server.__doc__ or ""
    assert "charter/digest" not in doc


def test_parse_active_reexec_units() -> None:
    rows = parse_active_reexec_units(
        "ulg-manage-reexec-1.service loaded active running desc\n"
        "ulg-manage-reexec-2.service loaded activating start desc\n"
        "other.service loaded active running desc\n"
    )
    assert rows == [
        "ulg-manage-reexec-1.service",
        "ulg-manage-reexec-2.service",
    ]


def test_read_env_local_subset_filters_keys(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text(
        "MANAGE_SOCKET=/a\nGATEWAY_DIR=/b\nOTHER=x\n",
        encoding="utf-8",
    )
    assert read_env_local_subset(tmp_path) == {
        "MANAGE_SOCKET": "/a",
        "GATEWAY_DIR": "/b",
    }


def test_build_child_env_uses_hub_layout(tmp_path: Path) -> None:
    env = build_child_env(tmp_path)
    assert env["PROJECT_ROOT"] == str(tmp_path)
    assert (
        env["PYTHONPATH"] == f"{tmp_path}/libs:{tmp_path}/services/universal-stargate"
    )


def test_build_systemd_run_cmd_absolute_python(tmp_path: Path) -> None:
    cmd = build_systemd_run_cmd(
        unit="ulg-manage-reexec-x.service",
        hub=tmp_path,
        child_argv=["/abs/py", "-m", "mod"],
        child_env=build_child_env(tmp_path),
    )
    assert cmd[0] == "systemd-run"
    assert "/abs/py" in cmd


def test_child_execute_argv_includes_deferred_report_flags(tmp_path: Path) -> None:
    argv = child_execute_argv(
        target_ref="deadbeef",
        tmux_target="0:0",
        result_path=tmp_path / "r.json",
        python_bin="/abs/python",
        parent_dispatch_id="parent-d-1",
        report_thread="15851",
        report_after_turn=1293,
        wait_parent_timeout_s=900,
    )
    blob = " ".join(argv)
    assert "CURSOR_SDK_DISPATCH_ID" not in blob
    assert "--parent-dispatch-id" in argv
    assert "parent-d-1" in argv
    assert "--report-thread" in argv
    assert "15851" in argv
    assert "--report-after-turn" in argv
    assert "1293" in argv
    assert "--wait-parent-timeout-s" in argv
    assert "900" in argv


def test_detach_passes_deferred_flags_to_systemd_child(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    result_path = hub / "tmp" / "result.json"
    captured_argv: list[list[str]] = []

    def run_cmd(
        cmd: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        del env
        if cmd[:3] == ["systemctl", "--user", "list-units"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "systemd-run":
            captured_argv.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    code, payload = launch_detach(
        target_ref="abc123",
        tmux_target=DEFAULT_TMUX_TARGET,
        hub=hub,
        result_path=result_path,
        run_cmd=run_cmd,
        python_bin="/abs/python",
        parent_dispatch_id="disp-99",
        report_thread="15851",
        report_after_turn=1300,
        wait_parent_timeout_s=900,
    )
    assert code == 0
    assert payload["status"] == "launched"
    assert len(captured_argv) == 1
    cmd_blob = " ".join(captured_argv[0])
    assert "disp-99" in cmd_blob
    assert "15851" in cmd_blob
    assert "1300" in cmd_blob
    assert "CURSOR_SDK_DISPATCH_ID" not in cmd_blob
