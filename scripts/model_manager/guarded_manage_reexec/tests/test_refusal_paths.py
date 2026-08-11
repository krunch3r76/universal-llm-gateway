"""Refusal-path unit tests for guarded manage reexec (no live manage).

AC: non-terminal intent, manage_inflight others, drain not clear — each asserts
refusal, not merely happy-path coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from scripts.model_manager.guarded_manage_reexec.checks import (
    collect_refuse_report,
    observe_drain_clear,
    observe_manage_inflight,
    observe_nonterminal_intents,
)
from scripts.model_manager.guarded_manage_reexec.runner import (
    prove_pickup,
    run_guarded_reexec,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    STATUS_PENDING_DRAIN,
    RestartIntentStore,
)


def _store(tmp_path: Any) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


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
        run_cmd=lambda cmd: (_ for _ in ()).throw(AssertionError(f"cmd {cmd}")),
    )
    assert result.status == "dry-run"
    assert result.reason == "checks_passed_stopped_before_quit"
    assert result.executed is False
    assert "charter_pause" not in calls
