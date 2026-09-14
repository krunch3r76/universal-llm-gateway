"""Regression tests for leaked-bridge restart outage (friction a:33561).

When a cursor-sdk bridge outlives its dispatch without ``CURSOR_SDK_DISPATCH_ID``,
the restart gate must not spin on full process scans or defer forever on garbage.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from services.git_integration_worker import admission as admission_mod
from services.git_integration_worker import cursor_sdk_orphan as orphan_mod
from services.git_integration_worker import cursor_sdk_restart_bridge_gate as gate_mod
from services.git_integration_worker import git_worker_drain_events as drain_events
from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_orphan import (
    live_bridge_occupancy,
    owned_live_bridge_occupancy,
    reset_live_bridge_occupancy_cache,
    sweep_min_age_s,
)
from services.git_integration_worker.cursor_sdk_restart_bridge_gate import (
    defer_restart_for_live_bridges,
    live_bridge_blocks_restart,
    reset_defer_log_throttle,
)


class _FakeBridgeProc:
    """psutil.Process stand-in for occupancy scans."""

    def __init__(
        self,
        pid: int,
        *,
        ppid: int | None = None,
        dispatch_id: str | None = None,
        is_bridge: bool = True,
        cwd: str | None = None,
    ) -> None:
        self.pid = pid
        self._ppid = ppid
        self.is_bridge = is_bridge
        self._env = (
            {} if dispatch_id is None else {"CURSOR_SDK_DISPATCH_ID": dispatch_id}
        )
        self._cwd = cwd

    def ppid(self) -> int | None:
        if self._ppid is None:
            raise orphan_mod.psutil.AccessDenied("denied")
        return self._ppid

    def cwd(self) -> str:
        if self._cwd is None:
            raise orphan_mod.psutil.AccessDenied("denied")
        return self._cwd

    def environ(self) -> dict[str, str]:
        return self._env


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_live_bridge_occupancy_cache()
    reset_defer_log_throttle()
    orphan_mod._occupancy_cache = None


def _install_occupancy(
    monkeypatch: pytest.MonkeyPatch,
    procs: list[_FakeBridgeProc],
    statuses: dict[str, str] | None = None,
) -> None:
    monkeypatch.setattr(
        orphan_mod.psutil,
        "process_iter",
        lambda attrs=None: list(procs),
    )
    monkeypatch.setattr(
        orphan_mod,
        "is_cursor_sdk_bridge_process",
        lambda proc: proc.is_bridge,
    )
    statuses = statuses or {}

    def _lookup(dispatch_id: str) -> dict[str, Any] | None:
        if dispatch_id not in statuses:
            return None
        return {"status": statuses[dispatch_id]}

    monkeypatch.setattr(orphan_mod, "_default_status_lookup", _lookup)


def test_ac1_wrapper_and_node_child_count_as_one_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 1: sh wrapper + node child collapse to a single occupancy row."""
    wrapper = _FakeBridgeProc(100, ppid=1, dispatch_id=None)
    node = _FakeBridgeProc(101, ppid=100, dispatch_id=None)
    _install_occupancy(monkeypatch, [wrapper, node])

    rows = live_bridge_occupancy(fresh=True)

    assert len(rows) == 1
    assert rows[0].pid == 100


def test_a33561_unowned_bridge_without_dispatch_id_does_not_block_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2 regression (a:33561): garbage bridge must not defer restart."""
    proc = _FakeBridgeProc(200, ppid=1, dispatch_id=None)
    _install_occupancy(monkeypatch, [proc])

    assert live_bridge_blocks_restart(force=True) is False
    assert live_bridge_blocks_restart(force=False) is False
    assert defer_restart_for_live_bridges(force=True, intent_id="0d2b0818") is False


def test_ac3_terminal_ledger_row_does_not_block_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2: bridge whose ledger row is terminal is not blocking."""
    proc = _FakeBridgeProc(201, ppid=1, dispatch_id="d-terminal")
    _install_occupancy(monkeypatch, [proc], {"d-terminal": "completed"})

    assert live_bridge_blocks_restart(force=True) is False
    assert owned_live_bridge_occupancy(fresh=True) == []


def test_ac4_owned_live_bridge_still_blocks_force_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2 guard: genuinely owned bridges still defer force restart."""
    proc = _FakeBridgeProc(202, ppid=1, dispatch_id="d-live")
    _install_occupancy(monkeypatch, [proc], {"d-live": "running"})

    assert live_bridge_blocks_restart(force=True) is True
    assert defer_restart_for_live_bridges(force=True, intent_id="intent-live") is True


def test_ac5_ledger_lookup_error_still_counts_as_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2 fail-safe: unreadable ledger must not be treated as unowned."""
    from sqlite3 import OperationalError

    proc = _FakeBridgeProc(203, ppid=1, dispatch_id="d-unknown")
    _install_occupancy(monkeypatch, [proc])

    def _boom(_dispatch_id: str) -> dict[str, Any] | None:
        raise OperationalError("database is locked")

    monkeypatch.setattr(orphan_mod, "_default_status_lookup", _boom)

    assert live_bridge_blocks_restart(force=True) is True
    assert len(owned_live_bridge_occupancy(fresh=True)) == 1


def test_ac6_occupancy_scan_cached_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 3a: repeated counts within TTL perform only one process scan."""
    proc = _FakeBridgeProc(204, ppid=1, dispatch_id=None)
    scan_count = 0

    def _iter(attrs=None):  # noqa: ANN001, ANN202
        nonlocal scan_count
        scan_count += 1
        return [proc]

    monkeypatch.setattr(orphan_mod.psutil, "process_iter", _iter)
    monkeypatch.setattr(
        orphan_mod,
        "is_cursor_sdk_bridge_process",
        lambda p: p.is_bridge,
    )
    monkeypatch.setattr(orphan_mod, "_OCCUPANCY_SCAN_TTL_S", 30.0)

    live_bridge_occupancy(fresh=True)
    live_bridge_occupancy()
    live_bridge_occupancy()

    assert scan_count == 1


def test_ac7_defer_log_and_event_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 3b: persistent deferral does not flood logs/events."""
    proc = _FakeBridgeProc(205, ppid=1, dispatch_id="d-live")
    _install_occupancy(monkeypatch, [proc], {"d-live": "running"})
    emitted: list[dict[str, Any]] = []
    logged: list[str] = []

    monkeypatch.setattr(
        gate_mod,
        "emit_sdk_restart_deferred_live_bridge",
        lambda **kwargs: emitted.append(kwargs),
    )
    monkeypatch.setattr(
        gate_mod.logger,
        "info",
        lambda msg, *args: logged.append(msg % args if args else msg),
    )
    monkeypatch.setattr(gate_mod, "_DEFER_LOG_INTERVAL_S", 3600.0)

    assert defer_restart_for_live_bridges(force=True, intent_id="0d2b0818") is True
    assert defer_restart_for_live_bridges(force=True, intent_id="0d2b0818") is True
    assert defer_restart_for_live_bridges(force=True, intent_id="0d2b0818") is True

    assert len(emitted) == 1
    assert len(logged) == 1


def test_fix4_sweep_min_age_shorter_while_restart_intent_pending() -> None:
    """Fix 4: drain-pending restart uses a shorter unowned-bridge grace."""
    assert sweep_min_age_s(restart_intent_pending=False) == orphan_mod._SWEEP_MIN_AGE_S
    assert (
        sweep_min_age_s(restart_intent_pending=True)
        == orphan_mod._SWEEP_MIN_AGE_DRAIN_S
    )
    assert orphan_mod._SWEEP_MIN_AGE_DRAIN_S < orphan_mod._SWEEP_MIN_AGE_S


def test_sweep_kills_unowned_bridge_at_drain_age_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 4 integration: unowned bridge is sweepable at drain grace, not 30m."""
    from services.git_integration_worker.cursor_sdk_orphan import sweep_unowned_bridges

    class _AgedProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.is_bridge = True
            self.killed = False
            self._create_time = time.time() - 120.0
            self._env: dict[str, str] = {}

        def create_time(self) -> float:
            return self._create_time

        def environ(self) -> dict[str, str]:
            return self._env

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    proc = _AgedProc(300)
    monkeypatch.setattr(
        orphan_mod.psutil,
        "process_iter",
        lambda attrs=None: [proc],
    )
    monkeypatch.setattr(
        orphan_mod,
        "is_cursor_sdk_bridge_process",
        lambda p: p.is_bridge,
    )
    monkeypatch.setattr(orphan_mod, "_default_status_lookup", lambda _d: None)

    drain_age = sweep_min_age_s(restart_intent_pending=True)
    assert sweep_unowned_bridges(min_age_s=drain_age).killed == [300]
    assert proc.killed is True

    proc2 = _AgedProc(301)
    monkeypatch.setattr(
        orphan_mod.psutil,
        "process_iter",
        lambda attrs=None: [proc2],
    )
    default_age = sweep_min_age_s(restart_intent_pending=False)
    assert sweep_unowned_bridges(min_age_s=default_age).killed == []
    assert proc2.killed is False


class _FakeSweepProc:
    """psutil.Process stand-in for the sweep path (needs age + kill)."""

    def __init__(self, pid: int, *, dispatch_id: str | None, age_s: float) -> None:
        self.pid = pid
        self._env = (
            {} if dispatch_id is None else {"CURSOR_SDK_DISPATCH_ID": dispatch_id}
        )
        self._age_s = age_s
        self.killed = False

    def create_time(self) -> float:
        return time.time() - self._age_s

    def environ(self) -> dict[str, str]:
        return self._env

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_sweep_invalidates_occupancy_cache_so_gate_sees_the_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A swept bridge must not keep blocking the gate from the TTL cache.

    The sweeper and the restart gate read the same roster. If the sweep kills a
    bridge but leaves the occupancy cache warm, the gate keeps counting the
    dead process until the TTL expires and defers another cycle — the sweeper
    and the gate disagreeing about one process, which is precisely the defect
    class a:33561 is about.
    """
    proc = _FakeSweepProc(pid=4242, dispatch_id=None, age_s=9999.0)
    monkeypatch.setattr(orphan_mod.psutil, "process_iter", lambda attrs=None: [proc])
    monkeypatch.setattr(orphan_mod, "is_cursor_sdk_bridge_process", lambda _proc: True)

    orphan_mod._occupancy_cache = (
        time.monotonic(),
        [orphan_mod.BridgeOccupancy(pid=4242, cwd="/repo", dispatch_id=None)],
    )

    result = orphan_mod.sweep_unowned_bridges(min_age_s=0.0)

    assert result.killed == [4242]
    assert proc.killed is True
    assert orphan_mod._occupancy_cache is None


def test_drain_completion_gate_exception_does_not_wedge_or_emit_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 2: bridge-gate ImportError must not 500, emit completed, or clear drain."""
    completed: list[dict] = []
    gate_failed: list[dict] = []
    monkeypatch.setattr(
        drain_events, "emit_drain_completed", lambda **k: completed.append(k)
    )
    monkeypatch.setattr(
        drain_events,
        "emit_drain_completion_gate_failed",
        lambda **k: gate_failed.append(k),
    )
    def _gate_import_error(**_kwargs: object) -> bool:
        raise ImportError("stale module graph")

    monkeypatch.setattr(
        admission_mod,
        "defer_restart_for_live_bridges",
        _gate_import_error,
    )

    controller = WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test-worker",
        pid=9999,
        worker_started_at="2026-09-14T00:00:00Z",
    )
    controller._draining = True
    controller._drain_epoch = 7
    controller._intent_id = "intent-gate"

    controller._maybe_emit_drain_completed()

    assert completed == []
    assert len(gate_failed) == 1
    assert gate_failed[0]["drain_epoch"] == 7
    assert gate_failed[0]["error_type"] == "ImportError"
    assert controller.is_draining() is True
    assert 7 not in controller._completed_epochs


def test_sweep_that_kills_nothing_leaves_the_cache_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op sweep must not throw away a valid cached roster.

    Invalidating on every sweep would reintroduce the full process-table scan
    that fix 3a exists to avoid.
    """
    monkeypatch.setattr(orphan_mod.psutil, "process_iter", lambda attrs=None: [])
    cached = (
        time.monotonic(),
        [orphan_mod.BridgeOccupancy(pid=99, cwd="/repo", dispatch_id="d1")],
    )
    orphan_mod._occupancy_cache = cached

    result = orphan_mod.sweep_unowned_bridges(min_age_s=0.0)

    assert result.killed == [] and result.kill_failed == []
    assert orphan_mod._occupancy_cache == cached
