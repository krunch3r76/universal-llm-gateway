"""Unit tests for kernel admission — arc_lane_too_weak mapping (P1-AC3)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import (
    kernel_tick,
    pickup_advance,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    CapStore,
    CapsView,
    EnvFacts,
    classify_shadow_diff,
    decide,
    map_old_skip_to_kernel,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.root_health import (
    AdmitResult,
    FireAttemptOutcome,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    SeedConfirm,
    Transition,
    load_root,
    seed_from_confirm,
    upsert_root,
)


def _idle_row(
    *,
    lane: str = "judgment",
    attendance: str = "autonomous",
    root_id: str = "5975",
) -> RootLedgerRow:
    return RootLedgerRow(
        root_id=root_id,
        status=RootStatus.IDLE,
        pickup_gid="G7" if root_id == "5975" else "G1",
        pickup_lane=lane,
        pickup_executor=None,
        attendance=attendance,
        scoreboard_uri=f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md",
    )


def _open_caps() -> CapsView:
    return CapsView(
        allowed=True,
        skip_reason=None,
        stopped_reason=None,
        revise_ok=True,
        revise_reason=None,
    )


@pytest.mark.offline
def test_arc_lane_too_weak_maps_to_queue_consult_when_autonomous_substrate_up() -> None:
    mapped = map_old_skip_to_kernel(
        "arc_lane_too_weak",
        attendance="autonomous",
        substrate_up=True,
    )
    assert mapped == Transition.QUEUE_CONSULT


@pytest.mark.offline
def test_arc_lane_too_weak_maps_to_defer_consult_when_substrate_down() -> None:
    mapped = map_old_skip_to_kernel(
        "arc_lane_too_weak",
        attendance="autonomous",
        substrate_up=False,
    )
    assert mapped == Transition.DEFER_CONSULT


@pytest.mark.offline
def test_decide_idle_judgment_autonomous_queues_consult() -> None:
    """No explicit cursor executor → still QUEUE_CONSULT (Policy B default)."""
    transition = decide(
        _idle_row(),
        EnvFacts(substrate_up=True, has_wip=False, attendance="autonomous"),
        _open_caps(),
    )
    assert transition == Transition.QUEUE_CONSULT


@pytest.mark.offline
def test_decide_idle_judgment_autonomous_cursor_executor_admits_worker() -> None:
    """a:27165 — tip/typed cursor/* on judgment breaks the force-consult loop."""
    row = replace(_idle_row(), pickup_executor="cursor/grok-4.6")
    transition = decide(
        row,
        EnvFacts(substrate_up=True, has_wip=False, attendance="autonomous"),
        _open_caps(),
    )
    assert transition == Transition.ADMIT_WORKER


@pytest.mark.offline
def test_decide_consult_pending_queues_even_with_cursor_executor() -> None:
    row = replace(_idle_row(), pickup_executor="cursor/grok-4.6")
    transition = decide(
        row,
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            consult_pending=True,
            tip_executor="cursor/grok-4.6",
        ),
        _open_caps(),
    )
    assert transition == Transition.QUEUE_CONSULT


@pytest.mark.offline
def test_decide_idle_judgment_autonomous_defers_when_substrate_down() -> None:
    transition = decide(
        _idle_row(),
        EnvFacts(substrate_up=False, has_wip=False, attendance="autonomous"),
        _open_caps(),
    )
    assert transition == Transition.DEFER_CONSULT


@pytest.mark.offline
def test_decide_attended_mechanical_admits_worker() -> None:
    transition = decide(
        _idle_row(lane="mechanical", attendance="attended", root_id="5993"),
        EnvFacts(substrate_up=True, has_wip=False, attendance="attended"),
        _open_caps(),
    )
    assert transition == Transition.ADMIT_WORKER


@pytest.mark.offline
def test_classify_shadow_arc_lane_too_weak_kernel_correct() -> None:
    label = classify_shadow_diff("arc_lane_too_weak", Transition.QUEUE_CONSULT)
    assert label == "kernel-correct"


@pytest.mark.offline
def test_classify_shadow_eligible_admit_worker_agree() -> None:
    label = classify_shadow_diff("eligible", Transition.ADMIT_WORKER)
    assert label == "agree"


@pytest.mark.offline
def test_classify_shadow_window_in_flight_noop_agree() -> None:
    label = classify_shadow_diff("window_in_flight", Transition.NOOP)
    assert label == "agree"


@pytest.mark.offline
def test_classify_shadow_skip_reason_noop_agree() -> None:
    label = classify_shadow_diff("no_checkpoint", Transition.NOOP)
    assert label == "agree"


@pytest.mark.offline
def test_classify_shadow_noop_noop_agree() -> None:
    label = classify_shadow_diff("noop", Transition.NOOP)
    assert label == "agree"


# ---- empty-hopper (a:26710) --------------------------------------------------


def _tip_body(row: str) -> str:
    return f"""\
# CHECKPOINT — agent-bus:6171

## In-flight / WIP
_None this window._

## Next-pickup
- {row}

## Steps
1. [ ] Standing wait

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""


def _consult_pending_tip_body(row: str) -> str:
    return f"""\
# CHECKPOINT — agent-bus:6237

## In-flight / WIP
_None this window._

## Next-pickup
- {row}

## Steps
1. [ ] R-admit consult pending

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""


def _turn(n: int, subject: str, body: str = "") -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.default_ledger_path",
        lambda: db,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.admission.caps.charter_runner_data_dir",
        lambda: tmp_path / "charter-runner-data",
    )
    for module in (kernel_tick, pickup_advance):
        monkeypatch.setattr(module, "write_cortex_mirror", lambda _row: "")
    conn = open_ledger_db(db)
    try:
        yield conn
    finally:
        conn.close()


def _seed_6171(conn, *, attendance: str = "attended") -> RootLedgerRow:
    return seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="6171",
            pickup_gid="G9",
            pickup_lane="judgment",
            pickup_executor="cursor/grok-4.6",
            attendance=attendance,
            scoreboard_uri="cortex://notes/system/threads/6171-charter-scoreboard.md",
        ),
    )


def _seed_6171_tip_authority(conn, *, attendance: str = "attended") -> RootLedgerRow:
    """Typed admit then strip ledger authority — exercises legacy tip-only kernel path."""
    row = _seed_6171(conn, attendance=attendance)
    upsert_root(conn, replace(row, pickup_gid=None))
    cleared = load_root(conn, "6171")
    assert cleared is not None
    return cleared


def _worker_admit_result(*, admitted: bool = True) -> AdmitResult:
    return AdmitResult(
        admitted=admitted,
        fire_attempt_outcome=(
            FireAttemptOutcome.FIRED if admitted else FireAttemptOutcome.REFUSED_PRE_FIRE
        ),
    )


def _env_6171(*, attendance: str = "attended") -> EnvSnapshot:
    return EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up"},
        attendance_by_root={"6171": attendance},
        scoreboard_pointer={},
        bus_tip_meta={"6171": {}},
    )


def _tick_6171(tmp_path: Path, turns: list[dict[str, Any]], *, attendance: str = "attended"):
    return asyncio.run(
        kernel_tick.apply_kernel_tick_for_root(
            "6171",
            turns,
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=tmp_path,
            env=_env_6171(attendance=attendance),
        )
    )


@pytest.mark.offline
def test_decide_empty_hopper_explicit_pending_noops() -> None:
    transition = decide(
        _idle_row(lane="judgment", attendance="attended", root_id="6171"),
        EnvFacts(substrate_up=True, has_wip=False, attendance="attended", empty_hopper=True),
        _open_caps(),
    )
    assert transition == Transition.NOOP


@pytest.mark.offline
def test_decide_empty_hopper_fences_autonomous_queue_consult() -> None:
    transition = decide(
        _idle_row(lane="judgment", attendance="autonomous", root_id="6171"),
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            empty_hopper=True,
        ),
        _open_caps(),
    )
    assert transition == Transition.NOOP


@pytest.mark.offline
@pytest.mark.parametrize(
    "row,expected",
    [
        ("G9 — standing wait · executor=pending", True),
        (
            "G9 — Marked standing wait (empty hopper) · executor=pending · "
            "executor_lane: judgment",
            True,
        ),
        ("G9 — standing wait · executor=", True),
        ("G9 — continue slice", False),
        ("G9 — implement · executor=cursor/grok-4.6", False),
        (
            "G2 — A + Gate-2 · executor=`cursor/grok-4.6` · executor_lane: judgment",
            False,
        ),
        (
            "G1 — work slice · executor=cursor/foo · executor_lane: implement",
            False,
        ),
    ],
)
def test_tip_is_empty_hopper_predicate(row: str, expected: bool) -> None:
    parsed = parse_checkpoint(_tip_body(row))
    assert (
        pickup_advance.tip_is_empty_hopper(parsed, has_wip=False, wip_window_id=None)
        is expected
    )


@pytest.mark.offline
def test_missing_executor_token_is_not_explicitly_unbound() -> None:
    live = pickup_advance.gated_pickup_from_parsed(
        parse_checkpoint(_tip_body("G9 — continue slice"))
    )
    assert live is not None
    assert pickup_advance.tip_executor_is_explicitly_unbound(live) is False


@pytest.mark.offline
def test_pending_executor_stays_worker_substrate_compatible() -> None:
    assert pickup_advance.worker_substrate_compatible("pending") is True


@pytest.mark.offline
def test_kernel_empty_hopper_skips_without_admit(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_6171_tip_authority(ledger)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("empty_hopper must not admit")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    monkeypatch.setattr(kernel_tick, "admit_consult_window", refuse_admit)
    outcome = _tick_6171(
        tmp_path,
        [_turn(3, "CHECKPOINT — standing wait", _tip_body("G9 — wait · executor=pending"))],
    )
    assert outcome.old_decision_label == "kernel_empty_hopper"
    assert outcome.skipped_reason == "empty_hopper"
    assert outcome.admitted is False


@pytest.mark.offline
def test_kernel_empty_hopper_stays_enrolled_second_tick(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_6171_tip_authority(ledger)

    async def freeze_pickup(conn, row, parsed, **kwargs):
        return row

    monkeypatch.setattr(kernel_tick, "_maybe_advance_pickup", freeze_pickup)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("empty_hopper must not admit")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    monkeypatch.setattr(kernel_tick, "admit_consult_window", refuse_admit)
    turns = [_turn(3, "CHECKPOINT — standing wait", _tip_body("G9 — wait · executor=pending"))]
    first = _tick_6171(tmp_path, turns)
    second = _tick_6171(tmp_path, turns)
    assert first.skipped_reason == "empty_hopper"
    assert second.skipped_reason == "empty_hopper"


@pytest.mark.offline
def test_kernel_concrete_executor_may_admit(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_6171(ledger)
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> AdmitResult:
        fired.append(True)
        return _worker_admit_result()

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick_6171(
        tmp_path,
        [
            _turn(
                3,
                "CHECKPOINT — work",
                _tip_body("G9 — implement slice · executor=cursor/grok-4.6"),
            )
        ],
    )
    assert outcome.admitted is True
    assert fired == [True]
    assert outcome.skipped_reason is None


@pytest.mark.offline
def test_kernel_missing_executor_token_fail_open_admits(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_6171(ledger)
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> AdmitResult:
        fired.append(True)
        return _worker_admit_result()

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick_6171(
        tmp_path,
        [_turn(3, "CHECKPOINT — work", _tip_body("G9 — continue slice"))],
    )
    assert outcome.admitted is True
    assert fired == [True]


@pytest.mark.offline
def test_kernel_empty_hopper_fences_autonomous_consult(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_6171_tip_authority(ledger, attendance="autonomous")
    queued: list[str] = []

    async def capture_queued(**payload: Any) -> None:
        queued.append(str(payload.get("root") or ""))

    monkeypatch.setattr(kernel_tick, "emit_consult_queued", capture_queued)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("empty_hopper must fence consult admits")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    monkeypatch.setattr(kernel_tick, "admit_consult_window", refuse_admit)
    outcome = _tick_6171(
        tmp_path,
        [_turn(3, "CHECKPOINT — standing wait", _tip_body("G9 — wait · executor=pending"))],
        attendance="autonomous",
    )
    assert outcome.skipped_reason == "empty_hopper"
    assert queued == []


@pytest.mark.offline
def test_tip_is_empty_hopper_consult_pending_is_not_empty() -> None:
    row = (
        "G3 — R-admit gate · CONSULT_PENDING · consult_role: r_admit · "
        "executor=pending · executor_lane: judgment"
    )
    parsed = parse_checkpoint(_consult_pending_tip_body(row))
    assert parsed.consult_pending is True
    assert (
        pickup_advance.tip_is_empty_hopper(parsed, has_wip=False, wip_window_id=None)
        is False
    )


@pytest.mark.offline
def test_decide_consult_queued_not_stranded_by_empty_hopper() -> None:
    row = RootLedgerRow(
        root_id="6237",
        status=RootStatus.CONSULT_QUEUED,
        pickup_gid="G3",
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/6237-charter-scoreboard.md",
    )
    transition = decide(
        row,
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            empty_hopper=True,
        ),
        _open_caps(),
    )
    assert transition == Transition.ADMIT_CONSULT


@pytest.mark.offline
def test_kernel_consult_pending_queues_consult_not_admit_worker(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = seed_from_confirm(
        conn=ledger,
        confirm=SeedConfirm(
            root_id="6237",
            pickup_gid="G3",
            pickup_lane="judgment",
            pickup_executor=None,
            attendance="attended",
            scoreboard_uri="cortex://notes/system/threads/6237-charter-scoreboard.md",
        ),
    )
    assert row.pickup_lane == "judgment"

    queued: list[str] = []

    async def capture_queued(**payload: Any) -> None:
        queued.append(str(payload.get("root") or ""))

    monkeypatch.setattr(kernel_tick, "emit_consult_queued", capture_queued)

    async def refuse_worker(**_kw: Any) -> bool:
        raise AssertionError("CONSULT_PENDING must not ADMIT_WORKER")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_worker)

    async def fake_consult(**_kw: Any) -> bool:
        return False

    monkeypatch.setattr(kernel_tick, "admit_consult_window", fake_consult)

    tip_row = (
        "G3 — R-admit gate · CONSULT_PENDING · consult_role: r_admit · "
        "executor=pending · executor_lane: judgment"
    )
    outcome = asyncio.run(
        kernel_tick.apply_kernel_tick_for_root(
            "6237",
            [
                _turn(
                    40,
                    "CHECKPOINT — CONSULT_PENDING",
                    _consult_pending_tip_body(tip_row),
                )
            ],
            caps=CapStore(intent_dir=tmp_path / "intent6237"),
            workspace_root=tmp_path,
            env=EnvSnapshot(
                giw_holder_lease={"held": False, "holder": None, "residue": None},
                propagation_residue={"kind": None, "detail": None},
                in_flight_windows=[],
                satellite_health={"cdp": "up"},
                attendance_by_root={"6237": "attended"},
                scoreboard_pointer={},
                bus_tip_meta={"6237": {}},
            ),
        )
    )
    assert outcome.skipped_reason != "empty_hopper"
    assert queued == ["6237"]
    assert outcome.old_decision_label != "kernel_empty_hopper"


def _g5b_fold_tip_body() -> str:
    return """\
# CHECKPOINT — agent-bus:6237

## In-flight / WIP
_None this window._

## Next pickup
- G5b — fold R-after amendments F1–F7 · lane=judgment · executor=pending · source_ref=todo:ulg-trigger-slice2-dynamic-predicates
- G6 — close · lane=judgment · after fold complete

## Steps
1. [x] G1 — Q
2. [x] G2 — A
3. [x] G3 — R-admit
4. [x] G4 — implement
5. [x] G5 — R-after
6. [ ] G5b — Fold R-after amendments F1–F7 · [judgment]
7. [ ] G6 — close

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): charter root.
"""


@pytest.mark.offline
def test_actionable_pending_g5b_not_empty_hopper() -> None:
    """6237 G5b — pending executor on aligned Steps row is bind-at-admit, not standing wait."""
    parsed = parse_checkpoint(_g5b_fold_tip_body())
    assert parsed.consult_pending is False
    assert pickup_advance.actionable_pickup_aligned(parsed) is True
    assert (
        pickup_advance.tip_is_empty_hopper(
            parsed, has_wip=False, wip_window_id=None
        )
        is False
    )


@pytest.mark.offline
def test_empty_hopper_row_rejections_for_standing_wait() -> None:
    parsed = parse_checkpoint(_tip_body("G9 — wait · executor=pending"))
    rejections = pickup_advance.empty_hopper_row_rejections(parsed)
    assert len(rejections) == 1
    assert rejections[0]["row_id"] == "G9"
    assert rejections[0]["predicate"] == "tip_executor_is_explicitly_unbound"


@pytest.mark.offline
def test_kernel_g5b_actionable_pending_admits(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_6171(ledger, attendance="attended")
    row = load_root(ledger, "6171")
    assert row is not None
    upsert_root(
        ledger,
        replace(row, pickup_gid="G5b", pickup_lane="judgment"),
    )
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> AdmitResult:
        fired.append(True)
        return AdmitResult(
            admitted=True,
            fire_attempt_outcome=FireAttemptOutcome.FIRED,
        )

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick_6171(
        tmp_path,
        [
            _turn(
                87,
                "CHECKPOINT — G5b fold",
                _g5b_fold_tip_body(),
            )
        ],
    )
    assert outcome.admitted is True
    assert fired == [True]
    assert outcome.skipped_reason != "empty_hopper"
