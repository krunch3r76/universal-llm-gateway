"""Join-half heal after LOOKUP_FAILED on dead watch registration_id (arc 7186)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence import (
    CapacityGateResult,
    fire_hop_for_decision,
)
from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorConfirmError,
    PredecessorVerdict,
    capture_predecessor_at_hop,
)
from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    REVOKE_BREAKER_N,
    breaker_blocks_hop,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    StandingHandoffFreshness,
    evaluate_watch,
    load_watches,
    mark_hop_fired,
    save_watches,
)

pytestmark = pytest.mark.offline

_THREAD = "7246"
_DEAD_REG = "83222a18-dead"
_NEW_REG = "ce1a4daa-live"
_PREDECESSOR_REG = "reg-predecessor"
_PREDECESSOR_EXEC = "exec-predecessor"
_COMMISSION_EXEC = "exec-commission"
_NOW = 1_700_000_000.0


def _watches_file(tmp_path: Path) -> Path:
    return tmp_path / "hop_cadence_watches.json"


def _op_row(
    *,
    execution_id: str,
    registration_id: str,
    thread_id: str = _THREAD,
) -> dict:
    return {
        "execution_id": execution_id,
        "registration_id": registration_id,
        "parent_thread": thread_id,
        "purpose": "operator-proxy",
        "status": "running",
    }


def _seed_watch(path: Path, *, registration_id: str = _DEAD_REG) -> None:
    save_watches(
        {
            _THREAD: {
                "thread_id": _THREAD,
                "registration_id": registration_id,
                "seated_at": _NOW - 2000.0,
                "from_agent": "web-anthropic",
            }
        },
        path,
    )


def test_heal_advances_registration_to_commission_row(tmp_path: Path) -> None:
    """Case 1: dead watch reg heals to the commission's live OP row."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    snap = {"rows": [_op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG)]}

    ok = mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=path,
        execution_id=_COMMISSION_EXEC,
        active_work_snap=snap,
        snapshot_reader=lambda: snap,
    )

    assert ok is False
    row = load_watches(path)[_THREAD]
    assert row["registration_id"] == _NEW_REG


def test_self_supersede_excluded_and_heal_still_advances(tmp_path: Path) -> None:
    """Case 2: successor-only snap must not record self as predecessor."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    snap = {"rows": [_op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG)]}
    release_calls: list[str] = []

    def _release_stub(handle, **_kwargs: object) -> dict:
        release_calls.append(handle.execution_id)
        return {"released": False}

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile."
        "release_superseded_on_confirm",
        side_effect=_release_stub,
    ):
        ok = mark_hop_fired(
            _THREAD,
            now=_NOW,
            path=path,
            execution_id=_COMMISSION_EXEC,
            active_work_snap=snap,
            snapshot_reader=lambda: snap,
        )

    assert ok is False
    row = load_watches(path)[_THREAD]
    assert row.get("superseded_execution_id") != _COMMISSION_EXEC
    assert _COMMISSION_EXEC not in release_calls
    assert row["registration_id"] == _NEW_REG


def test_predecessor_and_successor_both_present_resolves_predecessor(
    tmp_path: Path,
) -> None:
    """Case 3: genuine predecessor wins; successor is excluded from candidacy."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    snap = {
        "rows": [
            _op_row(
                execution_id=_PREDECESSOR_EXEC,
                registration_id=_PREDECESSOR_REG,
            ),
            _op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG),
        ]
    }

    ok = mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=path,
        execution_id=_COMMISSION_EXEC,
        active_work_snap=snap,
    )

    assert ok is True
    row = load_watches(path)[_THREAD]
    assert row["superseded_execution_id"] == _PREDECESSOR_EXEC
    assert row["superseded_execution_id"] != _COMMISSION_EXEC
    assert row["predecessor_verdict"] == PredecessorVerdict.INCUMBENT_RECORDED.value


def test_empty_snap_does_not_blank_registration_id(tmp_path: Path) -> None:
    """Case 4: no live row leaves registration_id unchanged, never emptied."""
    path = _watches_file(tmp_path)
    _seed_watch(path)

    ok = mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=path,
        execution_id=_COMMISSION_EXEC,
        active_work_snap={"rows": []},
        snapshot_reader=lambda: {"rows": []},
    )

    assert ok is False
    row = load_watches(path)[_THREAD]
    assert row["registration_id"] == _DEAD_REG


def test_no_matching_commission_row_leaves_registration_unchanged(
    tmp_path: Path,
) -> None:
    """Case 5: unrelated lane row must not heal via first-match."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    unrelated = _op_row(
        execution_id="exec-unrelated",
        registration_id="reg-other",
        thread_id="9999",
    )
    snap = {"rows": [unrelated]}

    ok = mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=path,
        execution_id=_COMMISSION_EXEC,
        active_work_snap=snap,
        snapshot_reader=lambda: snap,
    )

    assert ok is False
    assert load_watches(path)[_THREAD]["registration_id"] == _DEAD_REG


def test_t1_fallback_t2_heal_still_lands(tmp_path: Path) -> None:
    """Case 6: stale active_work_snap; heal reader supplies the commission row."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    heal_snap = {"rows": [_op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG)]}
    calls = {"n": 0}

    def _reader() -> dict:
        calls["n"] += 1
        return heal_snap

    ok = mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=path,
        execution_id=_COMMISSION_EXEC,
        active_work_snap={"rows": []},
        snapshot_reader=_reader,
    )

    assert ok is False
    assert calls["n"] >= 1
    assert load_watches(path)[_THREAD]["registration_id"] == _NEW_REG


@pytest.mark.asyncio
async def test_fire_hop_rereads_snapshot_after_commission(tmp_path: Path) -> None:
    """Case 7: post-commission reader call; successor not its own predecessor."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    pre_snap = {"rows": [], "free_slots": 3, "running_count": 0}
    post_snap = {"rows": [_op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG)]}
    reader_calls: list[str] = []

    def _reader() -> dict:
        reader_calls.append("call")
        if len(reader_calls) == 1:
            return pre_snap
        return post_snap

    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)

    async def _hop_ok(job, *, queue, incumbent=None):
        return {
            "ok": True,
            "reason": "continuity_hop_cdp_commissioned",
            "execution_id": _COMMISSION_EXEC,
        }

    monkeypatch_targets = cadence_mod
    with (
        patch.object(monkeypatch_targets, "run_continuity_hop_concurrent", new=_hop_ok),
        patch.object(
            monkeypatch_targets,
            "capacity_blocks_hop",
            lambda **_: CapacityGateResult.fail_open(),
        ),
        patch.object(
            monkeypatch_targets,
            "assess_standing_handoff",
            lambda tid: StandingHandoffFreshness(
                "current", f"cortex://x/{tid}.md", None, 1.0
            ),
        ),
    ):
        decision = HopDecision(
            thread_id=_THREAD,
            action="fire",
            reason="age_threshold_met",
            age_s=2000.0,
            threshold_s=1500.0,
            signal="watch_seated_at",
        )
        await fire_hop_for_decision(
            decision,
            queue=q,
            row={
                "from_agent": "web-anthropic",
                "registration_id": _DEAD_REG,
                "thread_id": _THREAD,
            },
            path=path,
            snapshot_reader=_reader,
        )

    assert len(reader_calls) >= 2
    row = load_watches(path)[_THREAD]
    assert row.get("superseded_execution_id") != _COMMISSION_EXEC
    assert row["registration_id"] == _NEW_REG


def test_breaker_still_counts_after_heal(tmp_path: Path) -> None:
    """Case 8: heals do not clear breaker; evaluate_watch still skips."""
    path = _watches_file(tmp_path)
    _seed_watch(path)
    empty = {"rows": []}

    for i in range(REVOKE_BREAKER_N):
        mark_hop_fired(
            _THREAD,
            now=_NOW + i * 1801.0,
            path=path,
            execution_id=_COMMISSION_EXEC,
            active_work_snap=empty,
            snapshot_reader=lambda: empty,
        )

    row = load_watches(path)[_THREAD]
    assert row.get("breaker_tripped") is True
    assert breaker_blocks_hop(row)
    decision = evaluate_watch(row, now=_NOW + REVOKE_BREAKER_N * 1801.0)
    assert decision.action == "skip"
    assert decision.reason == "revoke_breaker"


def test_identity_bound_never_writes_watch_ledger() -> None:
    """Case 9: admission identity-bound flow is observe-only."""
    save_calls: list[object] = []

    with (
        patch(
            "services.git_integration_worker.cursor_auto.hop_cadence_watch.save_watches",
            side_effect=lambda *a, **k: save_calls.append((a, k)),
        ),
        patch(
            "claude_bundles.hop_seat_cutover.load_watches",
            return_value={
                _THREAD: {
                    "thread_id": _THREAD,
                    "registration_id": _NEW_REG,
                }
            },
        ),
        patch(
            "claude_bundles.request_admission_identity._resolve_origin_cse_registration",
            return_value=_NEW_REG,
        ),
    ):
        from claude_bundles.request_admission_identity import gate_request_admission

        refusal = gate_request_admission(
            thread_id=_THREAD,
            caller_registration_id=_NEW_REG,
            active_work_snap={
                "rows": [
                    _op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG)
                ]
            },
        )

    assert refusal is None
    assert save_calls == []


def test_capture_excludes_successor_from_predecessor_resolution() -> None:
    """Regression guard: exclude_execution_id keeps successor out of capture."""
    row = {"thread_id": _THREAD, "registration_id": _DEAD_REG}
    snap = {"rows": [_op_row(execution_id=_COMMISSION_EXEC, registration_id=_NEW_REG)]}
    result = capture_predecessor_at_hop(
        row,
        snap,
        exclude_execution_id=_COMMISSION_EXEC,
    )
    assert isinstance(result, PredecessorConfirmError)
    assert result.reason == "predecessor_execution_lookup_failed"


def test_capture_excludes_successor_across_stargate_satellite_id_spaces() -> None:
    """Stargate exclude must not miss satellite snap rows (self-supersede class).

    Production hop fire holds a Stargate dashed UUID; active-work rows carry
    satellite hex. Same-string exclude cannot see the successor — this fixture
    is the gap the old regression missed.
    """
    stargate = "03908796-2e45-4a42-bce8-22b997117655"
    satellite = "45aff9ccfece4024be6650fa0a15e75b"
    row = {"thread_id": _THREAD, "registration_id": _DEAD_REG}
    snap = {"rows": [_op_row(execution_id=satellite, registration_id=_NEW_REG)]}

    # Dual-key exclude (production mark_hop_fired shape) ⇒ LOOKUP_FAILED.
    result = capture_predecessor_at_hop(
        row,
        snap,
        exclude_execution_ids={stargate, satellite},
    )
    assert isinstance(result, PredecessorConfirmError)
    assert result.reason == "predecessor_execution_lookup_failed"

    # Stargate-only exclude still misses the satellite row (pre-fix poison).
    poisoned = capture_predecessor_at_hop(
        row,
        snap,
        exclude_execution_id=stargate,
    )
    assert not isinstance(poisoned, PredecessorConfirmError)
    assert poisoned.registration_id == _NEW_REG
    assert poisoned.execution_id == satellite


def test_mark_hop_fired_resolves_satellite_and_avoids_self_supersede(
    tmp_path: Path,
) -> None:
    """mark_hop_fired must join Stargate→satellite and never persist self-supersede."""
    path = _watches_file(tmp_path)
    stargate = "03908796-2e45-4a42-bce8-22b997117655"
    satellite = "45aff9ccfece4024be6650fa0a15e75b"
    _seed_watch(path, registration_id=_DEAD_REG)
    snap = {"rows": [_op_row(execution_id=satellite, registration_id=_NEW_REG)]}

    mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=path,
        execution_id=stargate,
        active_work_snap=snap,
        snapshot_reader=lambda: snap,
    )
    watches = load_watches(path)
    row = watches[_THREAD]
    holder = str(row.get("registration_id") or "").strip()
    superseded = str(row.get("superseded_registration_id") or "").strip()
    assert not (holder and superseded and holder == superseded)

