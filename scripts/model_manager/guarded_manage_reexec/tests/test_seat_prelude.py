"""Seat-prelude behaviour: intent wait, GIW paired path, tmux pane resolve."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.guarded_manage_reexec.pane import (
    find_tmux_target_hosting_manage,
)
from scripts.model_manager.guarded_manage_reexec.runner import run_guarded_reexec
from scripts.model_manager.guarded_manage_reexec.seat_prelude import (
    _nudge_dead_intent_converge,
    giw_has_claimed_occupants,
    observe_giw_occupants,
    resolve_manage_inflight_for_seat,
    wait_nonterminal_intents_clear,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    RestartIntentStore,
)


def _store(tmp_path: Path) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


def test_timeout_derived_unhealthy_does_not_nudge_sync_restart() -> None:
    """AC1: unhealthy from a slow probe must not be treated as stopped."""
    calls: list[str] = []

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "health":
            return {
                "status": "unhealthy",
                "detail": "health probe failed: TimeoutError",
            }
        calls.append(method)
        return {}

    _nudge_dead_intent_converge(manage_call, [{"service": "cdp_ask"}])
    assert "sync_restart" not in calls


def test_unknown_health_does_not_nudge_sync_restart() -> None:
    calls: list[str] = []

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "health":
            return {"status": "unknown"}
        calls.append(method)
        return {}

    _nudge_dead_intent_converge(manage_call, [{"service": "mcp"}])
    assert "sync_restart" not in calls


def test_wait_nonterminal_intent_until_cleared(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent = store.create_intent(
        service="mcp",
        action="sync_restart",
        deadline_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        reason="fixture",
    )
    calls: list[str] = []

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del kwargs
        calls.append(method)
        if method == "health":
            return {"status": "stopped"}
        if method == "sync_restart":
            store.advance(intent.intent_id, status="completed", reason="test")
            return {"status": "ok"}
        return {}

    result = wait_nonterminal_intents_clear(
        store=store,
        manage_call=manage_call,
        timeout_s=5.0,
        poll_s=0.05,
    )
    assert result.cleared is True
    assert "sync_restart" in calls


def test_giw_paired_required_when_manage_child_and_no_occupants() -> None:
    busy = {
        "process": {"manage_inflight": 2, "activities": []},
        "services": {
            "git_integration_worker": {"active_work": {"active_count": 0}},
        },
    }

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        if method == "busy_status":
            return busy
        return {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "scripts.model_manager.guarded_manage_reexec.seat_prelude.giw_is_manage_child",
            lambda **_: True,
        )
        out = resolve_manage_inflight_for_seat(
            busy,
            manage_pid=1,
            manage_call=manage_call,
            timeout_s=0.1,
        )
    assert out.giw_paired_required is True
    assert out.cleared is True


def test_giw_paired_refused_when_occupants_claimed() -> None:
    busy = {
        "process": {"manage_inflight": 2, "activities": []},
        "services": {
            "git_integration_worker": {
                "active_work": {
                    "write_lease": {"holder_dispatch_id": "d-live"},
                },
            },
        },
    }
    assert giw_has_claimed_occupants(busy) is True

    def manage_call(method: str, params=None, **kwargs):  # noqa: ANN001
        del params, kwargs
        return busy if method == "busy_status" else {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "scripts.model_manager.guarded_manage_reexec.seat_prelude.giw_is_manage_child",
            lambda **_: True,
        )
        out = resolve_manage_inflight_for_seat(
            busy,
            manage_pid=1,
            manage_call=manage_call,
            timeout_s=0.1,
        )
    assert out.giw_paired_refused_occupants is True


def test_find_tmux_target_hosting_manage_scans_panes() -> None:
    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        assert cmd[:3] == ["tmux", "list-panes", "-a"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="0:0.0\t99\n0:4.0\t2340653\n",
            stderr="",
        )

    target, detail = find_tmux_target_hosting_manage(
        42,
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: pid == 42 and ancestor == 2340653,
    )
    assert target == "0:4.0"
    assert detail["matched_pane_pid"] == 2340653


def test_run_resolves_tmux_pane_instead_of_refusing(tmp_path: Path) -> None:
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

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            target = cmd[4]
            if target == "0:0":
                return subprocess.CompletedProcess(cmd, 0, stdout="99\n", stderr="")
            if target == "0:4.0":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="2340653\n", stderr=""
                )
        if cmd[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="0:0.0\t99\n0:4.0\t2340653\n",
                stderr="",
            )
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=True,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001
        tmux_target="0:0",
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: pid == 42 and ancestor == 2340653,
    )
    assert result.status == "dry-run"
    assert result.reason == "checks_passed_stopped_before_quit"
    assert result.checks["seat_prelude"]["tmux_target_effective"] == "0:4.0"


def test_dispatch_home_uses_operator_home_not_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    overlay = dispatch_root / "auto-seat-home"
    overlay.mkdir(parents=True)
    operator = tmp_path / "operator-home"
    operator.mkdir()
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    monkeypatch.setenv("HOME", str(overlay))
    monkeypatch.setattr(home_mod, "operator_real_home", lambda **_: operator)

    store = _store(tmp_path)

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
        raise AssertionError(method)

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=True,
        manage_call=manage_call,
        intent_db=store._db_path,  # noqa: SLF001
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
    )
    assert result.reason != "dispatch_home_host_refusal"
    assert result.checks["seat_prelude"]["seat_operator_home"] == str(operator)


_LIVE_OCCUPANT = {"write_lease": {"holder_dispatch_id": "d-live"}}


def _occupant_busy(*, active_work: dict[str, Any]) -> dict[str, Any]:
    return {
        "process": {"manage_inflight": 1, "activities": []},
        "charter_hold": {"held": False, "pause_drain_clear": False},
        "services": {"git_integration_worker": {"active_work": active_work}},
    }


def _recording_manage_call(busy: dict[str, Any], calls: list[str]):  # noqa: ANN202
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
            return busy
        if method == "charter_hold_status":
            return {
                "held": False,
                "pause_drain_clear": False,
                "tick_in_flight": False,
                "live_charter_shaped_dispatches": [],
            }
        if method == "charter_pause":
            return {"status": "ok", "held": True}
        raise AssertionError(method)

    return manage_call


def _pane_hosts_manage(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    if cmd[:2] == ["tmux", "display-message"]:
        return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
    raise AssertionError(cmd)


def _run(tmp_path: Path, busy: dict[str, Any], calls: list[str], *, dry_run: bool):
    return run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=dry_run,
        manage_call=_recording_manage_call(busy, calls),
        intent_db=_store(tmp_path)._db_path,  # noqa: SLF001
        run_cmd=_pane_hosts_manage,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
    )


def test_execute_refuses_live_occupant_with_zero_inflight_others(
    tmp_path: Path,
) -> None:
    """AC1/AC3: manage_inflight_others=0 and one live GIW occupant => refused."""
    calls: list[str] = []
    result = _run(
        tmp_path, _occupant_busy(active_work=_LIVE_OCCUPANT), calls, dry_run=False
    )
    assert result.checks["manage_inflight_others"] == 0
    assert result.status == "refused"
    assert result.reason == "giw_claimed_occupants"
    assert result.executed is False
    assert result.checks["findings"][0]["offenders"] == [
        {"occupant": "busy_status", "active_work": _LIVE_OCCUPANT}
    ]
    assert "charter_pause" not in calls
    assert "sync_restart" not in calls


def test_execute_counts_calling_dispatch_as_occupant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: the seat's own CURSOR_SDK_DISPATCH_ID is an occupant."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    calls: list[str] = []
    result = _run(
        tmp_path, _occupant_busy(active_work={"active_count": 0}), calls, dry_run=False
    )
    assert result.status == "refused"
    assert result.reason == "giw_claimed_occupants"
    assert result.checks["findings"][0]["offenders"] == [
        {"occupant": "caller_dispatch", "dispatch_id": "d-self"}
    ]
    assert "charter_pause" not in calls


def test_zero_occupants_passes_guard(tmp_path: Path) -> None:
    """AC3: no occupants and no caller dispatch => guard passes; execute proceeds."""
    busy = _occupant_busy(active_work={"active_count": 0})
    assert observe_giw_occupants(busy, caller_dispatch=None) is None
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=False)
    assert "charter_pause" in calls
    assert result.reason == "drain_not_clear_after_pause"


def test_dry_run_surfaces_occupant_refusal(tmp_path: Path) -> None:
    """AC2/AC3: dry-run reports that execute would refuse on occupants."""
    calls: list[str] = []
    result = _run(
        tmp_path, _occupant_busy(active_work=_LIVE_OCCUPANT), calls, dry_run=True
    )
    assert result.status == "dry-run"
    assert result.reason == "giw_claimed_occupants"
    assert result.checks["refused"] is True
    assert result.executed is False
    assert "charter_pause" not in calls
