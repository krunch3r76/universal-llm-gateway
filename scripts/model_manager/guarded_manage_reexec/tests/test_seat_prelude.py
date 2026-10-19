"""Seat-prelude behaviour: intent wait, GIW paired path, tmux pane resolve."""

from __future__ import annotations

import hashlib
import sqlite3
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
    InflightWaitResult,
    _foreign_giw_offenders,
    _nudge_dead_intent_converge,
    giw_foreign_claimed_occupants,
    giw_has_claimed_occupants,
    observe_giw_occupants,
    resolve_manage_inflight_for_seat,
    wait_nonterminal_intents_clear,
)
from scripts.model_manager.guarded_manage_reexec.tests.fixtures.giw_active_work_producer_shape import (
    PRODUCER_ACTIVE_WORK_FOREIGN_QUEUED,
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


def test_dry_run_self_lease_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: dry-run excludes caller when holder is self and count is 1."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": {
            "holders": {"standard": ["d-self"], "operator": []},
            "live_writers": 1,
        },
    }
    busy = _occupant_busy(active_work=active_work)
    assert observe_giw_occupants(busy, caller_dispatch="d-self", dry_run=True) is None
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=True)
    assert result.status == "dry-run"
    assert result.reason == "checks_passed_stopped_before_quit"
    assert result.executed is False


def test_dry_run_refuses_foreign_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: dry-run refuses a foreign write lease while env is d-self."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    active_work = {"write_lease": {"holder_dispatch_id": "d-foreign"}}
    busy = _occupant_busy(active_work=active_work)
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=True)
    assert result.status == "dry-run"
    assert result.reason == "giw_claimed_occupants"
    assert result.checks["refused"] is True


def test_dry_run_refuses_second_occupant_when_holder_is_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: dry-run refuses when active_count > 1 even if holder is caller."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 2,
    }
    busy = _occupant_busy(active_work=active_work)
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is True
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=True)
    assert result.reason == "giw_claimed_occupants"


def test_dry_run_refuses_foreign_queued_id_when_holder_is_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: dry-run refuses queued dispatch other than caller."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    active_work = PRODUCER_ACTIVE_WORK_FOREIGN_QUEUED
    busy = _occupant_busy(active_work=active_work)
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=True)
    assert result.reason == "giw_claimed_occupants"


def test_giw_foreign_claimed_occupants_refuses_producer_shape_queued_rows() -> None:
    """AC1: producer wire shape with foreign queued rows is a claim."""
    busy = _occupant_busy(active_work=PRODUCER_ACTIVE_WORK_FOREIGN_QUEUED)
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is True


def test_foreign_queued_fires_on_write_lease_rows_alone() -> None:
    """AC1 partial payload: write_lease.queued alone is enough to refuse."""
    active_work = {
        "write_lease": {
            "holder_dispatch_id": "d-self",
            "queue_depth": 1,
            "queued": [{"dispatch_id": "d-other"}],
        },
        "active_count": 1,
        "queued": 1,
    }
    busy = _occupant_busy(active_work=active_work)
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is True


def test_foreign_queued_fires_on_queued_dispatches_alone() -> None:
    """AC1 partial payload: queued_dispatches alone is enough to refuse."""
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "cursor_sdk_gate": {
            "busy_status": {
                "queue_depth": 1,
                "queued_dispatches": [{"dispatch_id": "d-other"}],
            }
        },
        "active_count": 1,
        "queued": 1,
    }
    busy = _occupant_busy(active_work=active_work)
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is True


def test_refuses_unattributable_queue_depth() -> None:
    """AC1: queue_depth > 0 with both row lists empty is an unattributable queue."""
    active_work = {
        "write_lease": {
            "holder_dispatch_id": "d-self",
            "queue_depth": 1,
            "queued": [],
        },
        "cursor_sdk_gate": {"busy_status": {"queue_depth": 1, "queued_dispatches": []}},
        "active_count": 1,
    }
    busy = _occupant_busy(active_work=active_work)
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is True


def test_queue_depth_with_caller_row_only_passes() -> None:
    """AC1: queue_depth > 0 does not refuse when the only row is the caller."""
    active_work = {
        "write_lease": {
            "holder_dispatch_id": "d-self",
            "queue_depth": 1,
            "queued": [{"dispatch_id": "d-self"}],
        },
        "cursor_sdk_gate": {
            "busy_status": {
                "active_holder": {"dispatch_id": "d-self"},
                "queue_depth": 1,
                "queued_dispatches": [{"dispatch_id": "d-self"}],
            }
        },
        "active_count": 1,
        "queued": 1,
    }
    busy = _occupant_busy(active_work=active_work)
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is False


def test_dry_run_refuses_unattributable_single_active_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: count 1 with no positive caller attribution refuses."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    active_work = {
        "write_lease": {"holder_dispatch_id": None, "active_holders": []},
        "active_count": 1,
        "active_ops": [],
    }
    busy = _occupant_busy(active_work=active_work)
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=True)
    assert result.reason == "giw_claimed_occupants"


def test_dry_run_caller_only_in_active_holders_passes() -> None:
    """AC2: holder_dispatch_id on active_holders identifies the caller.

    Condition (2): d-self is not in holders, and live_writers is 0. Empty
    holder lists satisfy the presence check. Putting d-self in holders would
    mask a break in active_holders attribution.
    """
    active_work = {
        "write_lease": {
            "holder_dispatch_id": None,
            "active_holders": [{"holder_dispatch_id": "d-self"}],
        },
        "active_count": 1,
        "cursor_sdk_gate": {
            "holders": {"standard": [], "operator": []},
            "live_writers": 0,
        },
    }
    busy = _occupant_busy(active_work=active_work)
    assert observe_giw_occupants(busy, caller_dispatch="d-self", dry_run=True) is None


def test_dry_run_caller_identified_in_active_ops_passes() -> None:
    """AC2: active_ops op_id for kind cursor_sdk identifies the caller.

    Condition (2): d-self is not in holders, and live_writers is 0. The
    caller is attributed only through active_ops.
    """
    active_work = {
        "write_lease": {"holder_dispatch_id": None},
        "active_count": 1,
        "active_ops": [{"kind": "cursor_sdk", "op_id": "d-self"}],
        "cursor_sdk_gate": {
            "holders": {"standard": [], "operator": []},
            "live_writers": 0,
        },
    }
    busy = _occupant_busy(active_work=active_work)
    assert observe_giw_occupants(busy, caller_dispatch="d-self", dry_run=True) is None


def test_dry_run_offender_shape_lists_excluded_and_foreign_only() -> None:
    """AC3: dry-run refusal names the caller and foreign rows only."""
    busy = _occupant_busy(active_work=PRODUCER_ACTIVE_WORK_FOREIGN_QUEUED)
    finding = observe_giw_occupants(busy, caller_dispatch="d-self", dry_run=True)
    assert finding is not None
    offenders = finding.offenders
    assert len(offenders) == 1
    row = offenders[0]
    assert row["occupant"] == "busy_status"
    assert row["excluded_dispatch_id"] == "d-self"
    assert "active_work" not in row
    assert row["foreign"]
    assert all(item.get("dispatch_id") != "d-self" for item in row["foreign"])
    doc = _foreign_giw_offenders.__doc__ or ""
    assert "foreign" in doc
    assert "excluded_dispatch_id" in doc
    assert "never the excluded dispatch id" not in doc


def test_producer_shape_fixture_matches_active_work_keys() -> None:
    """AC7: fixture keys are the /active-work content keys; queued is an int."""
    content_keys = {
        "running",
        "queued",
        "cursor_dispatches",
        "cursor_sdk_gate",
        "write_lease",
        "active_count",
        "active_ops",
        "cse_holders",
        "cse_holder",
        "busy",
    }
    fixture = PRODUCER_ACTIVE_WORK_FOREIGN_QUEUED
    assert set(fixture) <= content_keys
    assert isinstance(fixture["queued"], int)
    assert fixture["write_lease"]["queued"][0]["dispatch_id"] == "d-other"
    assert (
        fixture["cursor_sdk_gate"]["busy_status"]["queued_dispatches"][0]["dispatch_id"]
        == "d-other"
    )
    assert fixture["write_lease"]["holder_dispatch_id"] == "d-self"


def test_giw_has_claimed_occupants_ignores_queued_only_state() -> None:
    """AC8: queued-only state is foreign for dry-run and invisible to execute."""
    active_work = {
        "active_count": 0,
        "total": 0,
        "running_count": 0,
        "running": 0,
        "write_lease": {
            "holder_dispatch_id": None,
            "queue_depth": 1,
            "queued": [{"dispatch_id": "d-other"}],
        },
        "cursor_sdk_gate": {
            "busy_status": {
                "queue_depth": 1,
                "queued_dispatches": [{"dispatch_id": "d-other"}],
            }
        },
    }
    busy = _occupant_busy(active_work=active_work)
    assert giw_has_claimed_occupants(busy) is False
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is True


def test_dry_run_missing_giw_service_refuses_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing GIW service is unreadable on dry-run, not a pass.

    AC5 (c47bafdfe, "gate exclude-self to dry_run only") asserted that
    observe_giw_occupants returns None when services is {}. It existed so
    the exclude-self dry-run would not invent giw_claimed_occupants from an
    absent services.git_integration_worker entry: giw_foreign_claimed_occupants
    is false when that key is missing, and the old dry-run treated that false
    as clear. That pass hid a GIW that did not answer. Absence is now
    giw_occupancy_unreadable. It is still not a foreign claim.
    """
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    busy = {
        "process": {"manage_inflight": 1, "activities": []},
        "services": {},
    }
    assert giw_foreign_claimed_occupants(busy, exclude_dispatch_id="d-self") is False
    finding = observe_giw_occupants(busy, caller_dispatch="d-self", dry_run=True)
    assert finding is not None
    assert finding.reason == "giw_occupancy_unreadable"


def test_execute_empty_active_work_keeps_caller_and_skips_paired_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4: execute counts caller when active_work is empty; no paired stop."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "d-self")
    paired_stop_calls: list[bool] = []
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.run_giw_paired_stop",
        lambda *_a, **_k: paired_stop_calls.append(True) or {"status": "ok"},
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.runner.resolve_manage_inflight_for_seat",
        lambda *_a, **_k: InflightWaitResult(
            cleared=True, waited_s=0.0, giw_paired_required=True
        ),
    )
    busy = _occupant_busy(active_work={})
    calls: list[str] = []
    result = _run(tmp_path, busy, calls, dry_run=False)
    assert result.status == "refused"
    assert result.reason == "giw_claimed_occupants"
    assert result.checks["findings"][0]["offenders"] == [
        {"occupant": "caller_dispatch", "dispatch_id": "d-self"}
    ]
    assert paired_stop_calls == []


def test_zero_occupants_passes_guard(tmp_path: Path) -> None:
    """AC3: no occupants => guard passes; execute proceeds without charter pause."""
    busy = _occupant_busy(active_work={"active_count": 0})
    assert observe_giw_occupants(busy, caller_dispatch=None) is None
    calls: list[str] = []

    def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["tmux", "display-message"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="9\n", stderr="")
        if cmd[:2] == ["tmux", "split-window"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="%1\n", stderr="")
        if cmd[:2] == ["tmux", "kill-pane"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    result = run_guarded_reexec(
        target_ref="deadbeef",
        dry_run=False,
        manage_call=_recording_manage_call(busy, calls),
        intent_db=_store(tmp_path)._db_path,  # noqa: SLF001
        run_cmd=run_cmd,
        tree_contains_fn=lambda pid, ancestor: pid == ancestor,
    )
    assert "charter_pause" not in calls
    assert "charter_hold_status" not in calls
    assert result.status == "refused"


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


def _readable_gate(
    *,
    standard: list[str] | None = None,
    operator: list[str] | None = None,
    live_writers: int = 0,
) -> dict[str, Any]:
    return {
        "holders": {
            "standard": list(standard or []),
            "operator": list(operator or []),
        },
        "live_writers": live_writers,
    }


def _foreign_ids(finding: Any) -> list[dict[str, Any]]:
    assert finding is not None
    return finding.offenders[0]["foreign"]


def test_two_writers_sibling_foreign_via_holders_and_ledger() -> None:
    """(a) exclude one of two writers: sibling is foreign via holders and ledger."""
    via_holders = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self", "d-sib"], live_writers=2),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=via_holders),
        caller_dispatch="d-self",
        dry_run=True,
    )
    assert any(
        row.get("dispatch_id") == "d-sib"
        and row.get("source") == "cursor_sdk_gate.holders"
        for row in _foreign_ids(finding)
    )
    sole = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=1),
    }

    def reader() -> list[dict[str, str]]:
        return [
            {"holder_dispatch_id": "d-self"},
            {"holder_dispatch_id": "d-sib"},
        ]

    finding = observe_giw_occupants(
        _occupant_busy(active_work=sole),
        caller_dispatch="d-self",
        dry_run=True,
        ledger_reader=reader,
    )
    assert any(
        row.get("dispatch_id") == "d-sib" and row.get("source") == "ledger_reader"
        for row in _foreign_ids(finding)
    )


def test_lane_b_sibling_absent_from_keyed_lease_is_foreign() -> None:
    """(b) Lane-B writer missing from the Lane-A write_lease is still foreign."""
    active_work = {
        "write_lease": {
            "holder_dispatch_id": "d-self",
            "active_holders": [],
            "queued": [],
        },
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(
            standard=["d-self"], operator=["d-lane-b"], live_writers=2
        ),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    assert any(row.get("dispatch_id") == "d-lane-b" for row in _foreign_ids(finding))


def test_sole_writer_dry_run_has_no_foreign_rows() -> None:
    """(c) the caller alone is not a foreign occupant."""
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=1),
    }
    assert (
        observe_giw_occupants(
            _occupant_busy(active_work=active_work),
            caller_dispatch="d-self",
            dry_run=True,
        )
        is None
    )


def test_conductor_in_active_holders_stays_foreign() -> None:
    """(d) a conductor row other than the caller is still foreign."""
    active_work = {
        "write_lease": {
            "holder_dispatch_id": "d-self",
            "active_holders": [{"holder_dispatch_id": "d-conductor"}],
        },
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=1),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    assert any(
        row.get("dispatch_id") == "d-conductor"
        and row.get("source") == "write_lease.active_holders"
        for row in _foreign_ids(finding)
    )


def test_live_writers_above_one_attributed_caller_is_foreign() -> None:
    """(e) live_writers=2 with one attributed caller is a foreign count."""
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=2),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    assert any(
        row.get("source") == "cursor_sdk_gate.live_writers" and row.get("count") == 2
        for row in _foreign_ids(finding)
    )


def test_dry_run_holders_missing_refuses_unreadable() -> None:
    """(f) a payload with no foreign rows and no holders is unreadable."""
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    assert finding is not None
    assert finding.reason == "giw_occupancy_unreadable"


def test_read_live_write_holders_missing_path_raises(tmp_path: Path) -> None:
    """(g) a missing ledger path raises and creates neither the file nor its dir."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        LedgerUnreadable,
        read_live_write_holders,
    )

    missing = tmp_path / "no-such-dir" / "ledger.db"
    assert not missing.parent.exists()
    with pytest.raises(LedgerUnreadable):
        read_live_write_holders(missing)
    assert not missing.exists()
    assert not missing.parent.exists()


def test_dry_run_ledger_reader_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(h) an injected read-only reader leaves the ledger bytes and CSE rows."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
        read_live_write_holders,
    )

    db = tmp_path / "cursor-sdk-dispatch.db"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO cse_session_holders ("
            "holder_id, chat_url, seat_state, bound_at, last_transition_at"
            ") VALUES (?, ?, ?, ?, ?)",
            ("h1", "https://claude.ai/c/1", "driving", "t0", "t0"),
        )
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches ("
            "dispatch_id, fingerprint, thread_id, resolved_model, status, "
            "message_present, record_json, read_only"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("d-self", "fp", "t-self", "cursor/grok-4.7", "running", 0, "{}", 0),
        )
        conn.commit()

    def fingerprint() -> tuple[str, int, int]:
        data = db.read_bytes()
        st = db.stat()
        return hashlib.sha256(data).hexdigest(), st.st_size, st.st_mtime_ns

    def cse_rows() -> list[tuple[Any, ...]]:
        uri = f"file:{db.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return conn.execute(
                "SELECT holder_id, seat_state FROM cse_session_holders "
                "ORDER BY holder_id"
            ).fetchall()
        finally:
            conn.close()

    before = fingerprint()
    rows_before = cse_rows()
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=1),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
        ledger_reader=lambda: read_live_write_holders(db),
    )
    assert finding is None
    assert fingerprint() == before
    assert cse_rows() == rows_before
    CursorDispatchLedger._instance = None


def test_ledger_reader_without_exclude_refuses() -> None:
    """(i) ledger rows that do not name the caller refuse."""
    active_work = {
        "write_lease": {"holder_dispatch_id": "d-self"},
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=1),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
        ledger_reader=lambda: [{"holder_dispatch_id": "d-other"}],
    )
    assert finding is not None
    assert finding.reason == "giw_claimed_occupants"
    assert any(row.get("source") == "ledger_reader" for row in _foreign_ids(finding))


def test_running_writer_not_in_holders_is_foreign() -> None:
    """(j) a writer in active_ops and live_writers, absent from holders, is foreign."""
    active_work = {
        "active_ops": [
            {"kind": "cursor_sdk", "op_id": "d-self"},
            {"kind": "cursor_sdk", "op_id": "d-running"},
        ],
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=2),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    foreign = _foreign_ids(finding)
    assert any(
        row.get("dispatch_id") == "d-running" and row.get("source") == "active_ops"
        for row in foreign
    )
    assert any(row.get("source") == "cursor_sdk_gate.live_writers" for row in foreign)


def test_read_only_caller_with_foreign_holder_refuses_via_holders() -> None:
    """(k) read-only caller in holders and active_ops; foreign writer in holders."""
    active_work = {
        "active_ops": [{"kind": "cursor_sdk", "op_id": "d-self"}],
        "cursor_sdk_gate": _readable_gate(
            standard=["d-self", "d-foreign"], live_writers=1
        ),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    foreign = _foreign_ids(finding)
    assert any(
        row.get("dispatch_id") == "d-foreign"
        and row.get("source") == "cursor_sdk_gate.holders"
        for row in foreign
    )
    assert all(row.get("source") != "cursor_sdk_gate.live_writers" for row in foreign)


def test_holder_only_writer_with_active_count_one_passes() -> None:
    """(k2) writer caller in holders only, live_writers=1, active_count=1, passes."""
    active_work = {
        "active_count": 1,
        "cursor_sdk_gate": _readable_gate(standard=["d-self"], live_writers=1),
    }
    assert (
        observe_giw_occupants(
            _occupant_busy(active_work=active_work),
            caller_dispatch="d-self",
            dry_run=True,
        )
        is None
    )


def test_dead_heartbeat_still_refuses_through_holders_and_live_writers() -> None:
    """(l) dead_heartbeat clears active_ops; holders and live_writers still refuse."""
    active_work = {
        "busy": False,
        "active_count": 0,
        "active_ops": [],
        "determination": "dead_heartbeat",
        "dead_heartbeat_ops": [{"kind": "cursor_sdk", "op_id": "d-dead"}],
        "cursor_sdk_gate": _readable_gate(standard=["d-foreign"], live_writers=1),
    }
    finding = observe_giw_occupants(
        _occupant_busy(active_work=active_work),
        caller_dispatch="d-self",
        dry_run=True,
    )
    foreign = _foreign_ids(finding)
    assert any(
        row.get("dispatch_id") == "d-foreign"
        and row.get("source") == "cursor_sdk_gate.holders"
        for row in foreign
    )
    assert any(
        row.get("source") == "cursor_sdk_gate.live_writers" and row.get("count") == 1
        for row in foreign
    )
