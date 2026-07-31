"""Identical-work refire gate — AC-1…AC-10 offline spine."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.identical_work_refire import (
    FRICTION_ID,
    SKIP_REASON,
    RefireGateContext,
    evaluate_identical_work_refire,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.window_sequence import (
    release_window_on_harvest,
    window_id_for,
)
from scripts.model_manager.ui.controller.charter_runner.work_key import compute_work_key
from scripts.model_manager.ui.controller.charter_runner.work_key_store import (
    find_record,
    live_undispositioned_for_key,
    record_admit,
    stamp_disposition,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    SourceRefConflict,
)
from services.git_integration_worker.tests.test_cursor_dispatch_ledger import (
    _admit,
    _req,
)

_ROOT = "6489"
_SOURCE_REF = "todo:layer-native-dogfood-g4"
_GIW_PAYLOAD = {
    "busy": True,
    "cursor_dispatches": {"dispatch_ids": ["dispatch-w1"]},
}


def _row(**overrides) -> RootLedgerRow:
    base = RootLedgerRow(
        root_id=_ROOT,
        status=RootStatus.IDLE,
        pickup_gid="G1",
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/6489-scoreboard.md",
    )
    return RootLedgerRow(**{**base.__dict__, **overrides})


def _work_key(*, consult_role: str = "judgment_gap", admission_mode: str = "consult") -> str:
    return compute_work_key(
        root_id=_ROOT,
        source_ref=_SOURCE_REF,
        pickup_gid="G1",
        consult_role=consult_role,
        admission_mode=admission_mode,
        pickup_lane="judgment",
    )


@pytest.fixture
def ledger_conn(tmp_path: Path):
    conn = open_ledger_db(tmp_path / "root-ledger.sqlite")
    row = _row()
    upsert_root(conn, row)
    yield conn
    conn.close()


def _seed_live_holder(
    conn,
    *,
    window_index: int = 1,
    dispatch_id: str = "dispatch-w1",
    consult_role: str = "judgment_gap",
) -> str:
    work_key = _work_key(consult_role=consult_role)
    window_id = window_id_for(_ROOT, window_index)
    record_admit(
        conn,
        work_key=work_key,
        root_id=_ROOT,
        window_id=window_id,
        dispatch_id=dispatch_id,
        thread_id="thread-w1",
        admitted_at=time.time() - 30.0,
    )
    return work_key


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac1_6489_dual_admit_refused(ledger_conn) -> None:
    _seed_live_holder(ledger_conn)
    row = _row(status=RootStatus.IDLE, wip_window_id=None)
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=row,
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
    )
    assert outcome.refused is True
    assert outcome.skipped_reason == SKIP_REASON
    assert FRICTION_ID == 27259


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac2_arc_lane_change_does_not_evade(ledger_conn) -> None:
    """arc_lane is excluded from work_key — path_sim vs layer must not evade."""
    work_key = _seed_live_holder(ledger_conn)
    row = _row(status=RootStatus.IDLE, wip_window_id=None)
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=row,
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
    )
    assert outcome.refused is True
    assert outcome.work_key == work_key


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac3_ledger_reset_does_not_clear_identity(ledger_conn) -> None:
    _seed_live_holder(ledger_conn)
    upsert_root(
        ledger_conn,
        _row(status=RootStatus.IDLE, wip_window_id=None),
    )
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(status=RootStatus.IDLE, wip_window_id=None),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
    )
    assert outcome.refused is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac4_of2_same_window_resume_not_refused(ledger_conn) -> None:
    _seed_live_holder(ledger_conn, window_index=1, dispatch_id="dispatch-w1")
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(status=RootStatus.CONSULT_ADMITTED, wip_window_id="charter-6489-w1"),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
        ctx=RefireGateContext(
            incoming_window_index=1,
            incoming_dispatch_id="dispatch-w1",
        ),
    )
    assert outcome.refused is False
    assert outcome.carve_out == "of2_resume"


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ctx", "expected_carve_out"),
    [
        (RefireGateContext(force=True), "force"),
        (RefireGateContext(nest_under="dispatch-w1"), "nest"),
        (RefireGateContext(supersede_window_id="charter-6489-w1"), "supersede"),
    ],
)
async def test_ac5_carve_outs(
    ledger_conn,
    ctx: RefireGateContext,
    expected_carve_out: str,
) -> None:
    _seed_live_holder(ledger_conn)
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
        ctx=ctx,
    )
    assert outcome.refused is False
    assert outcome.carve_out == expected_carve_out
    if expected_carve_out == "supersede":
        record = find_record(
            ledger_conn,
            work_key=_work_key(),
            window_id="charter-6489-w1",
        )
        assert record is not None
        assert record.disposition == "superseded"


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pickup_gid", "consult_role", "root_id", "source_ref", "pickup_lane"),
    [
        ("G2", "judgment_gap", _ROOT, _SOURCE_REF, "judgment"),
        ("G1", "r_admit", _ROOT, _SOURCE_REF, "judgment"),
        ("G1", "judgment_gap", "7000", _SOURCE_REF, "judgment"),
        ("G1", "judgment_gap", _ROOT, "todo:other-work", "judgment"),
        ("G1", "judgment_gap", _ROOT, _SOURCE_REF, "implement"),
    ],
)
async def test_ac6_distinct_work_not_refused(
    ledger_conn,
    pickup_gid: str,
    consult_role: str,
    root_id: str,
    source_ref: str,
    pickup_lane: str,
) -> None:
    _seed_live_holder(ledger_conn)
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(pickup_gid=pickup_gid, root_id=root_id, pickup_lane=pickup_lane),
        root_id=root_id,
        transition=Transition.ADMIT_CONSULT,
        source_ref=source_ref,
        consult_role=consult_role,
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
    )
    assert outcome.refused is False


@pytest.mark.offline
@pytest.mark.asyncio
async def test_same_gid_densify_harvest_allows_implement(ledger_conn) -> None:
    """6563 class: G4 judgment densify harvested must not fence G4 implement."""
    work_key = compute_work_key(
        root_id=_ROOT,
        source_ref=_SOURCE_REF,
        pickup_gid="G4",
        consult_role=None,
        admission_mode="autonomous",
        pickup_lane="judgment",
    )
    record_admit(
        ledger_conn,
        work_key=work_key,
        root_id=_ROOT,
        window_id=window_id_for(_ROOT, 2),
        dispatch_id="dispatch-w2",
        thread_id="thread-w2",
        admitted_at=time.time() - 30.0,
    )
    stamp_disposition(
        ledger_conn,
        work_key=work_key,
        window_id=window_id_for(_ROOT, 2),
        disposition="harvested",
    )
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(pickup_gid="G4", pickup_lane="implement"),
        root_id=_ROOT,
        transition=Transition.ADMIT_WORKER,
        source_ref=_SOURCE_REF,
        consult_role=None,
        admission_mode="autonomous",
        giw_payload={"busy": False, "cursor_dispatches": {"dispatch_ids": []}},
    )
    assert outcome.refused is False
    assert outcome.work_key != work_key


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac7_harvested_same_key_refused(ledger_conn) -> None:
    """Path B (6486): harvested same work_key fences re-admit."""
    work_key = _seed_live_holder(ledger_conn)
    stamp_disposition(
        ledger_conn,
        work_key=work_key,
        window_id="charter-6489-w1",
        disposition="harvested",
    )
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload={"busy": False, "cursor_dispatches": {"dispatch_ids": []}},
    )
    assert outcome.refused is True
    assert outcome.skipped_reason == SKIP_REASON


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ex_gap_6518_post_harvest_refire(ledger_conn) -> None:
    """6518 class: w1 harvested, empty GIW — w2 same-key ADMIT refused."""
    work_key = _seed_live_holder(ledger_conn, window_index=1, dispatch_id="dispatch-w1")
    stamp_disposition(
        ledger_conn,
        work_key=work_key,
        window_id="charter-6489-w1",
        disposition="harvested",
    )
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(status=RootStatus.IDLE, wip_window_id=None),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload={"busy": False, "cursor_dispatches": {"dispatch_ids": []}},
    )
    assert outcome.refused is True
    assert outcome.skipped_reason == SKIP_REASON
    assert outcome.work_key == work_key


@pytest.mark.offline
@pytest.mark.asyncio
async def test_failed_disposition_allows_retry(ledger_conn) -> None:
    work_key = _seed_live_holder(ledger_conn)
    stamp_disposition(
        ledger_conn,
        work_key=work_key,
        window_id="charter-6489-w1",
        disposition="failed",
    )
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload={"busy": False, "cursor_dispatches": {"dispatch_ids": []}},
    )
    assert outcome.refused is False


@pytest.mark.offline
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe_status", "refused"),
    [
        ("degraded", False),
        ("error", True),
    ],
)
async def test_ac8_probe_degradation_posture(
    ledger_conn,
    probe_status: str,
    refused: bool,
) -> None:
    _seed_live_holder(ledger_conn)
    if probe_status == "degraded":
        with patch(
            "scripts.model_manager.ui.controller.charter_runner.identical_work_refire.read_giw_active_work",
            new_callable=AsyncMock,
            return_value=type(
                "R",
                (),
                {"status": "degraded", "error_class": "ConnectError"},
            )(),
        ):
            outcome = await evaluate_identical_work_refire(
                ledger_conn,
                row=_row(),
                root_id=_ROOT,
                transition=Transition.ADMIT_CONSULT,
                source_ref=_SOURCE_REF,
                consult_role="judgment_gap",
                admission_mode="autonomous",
            )
    else:
        with patch(
            "scripts.model_manager.ui.controller.charter_runner.identical_work_refire.read_giw_active_work",
            new_callable=AsyncMock,
            return_value=type("R", (), {"status": "error", "error_class": "Timeout"})(),
        ):
            outcome = await evaluate_identical_work_refire(
                ledger_conn,
                row=_row(),
                root_id=_ROOT,
                transition=Transition.ADMIT_CONSULT,
                source_ref=_SOURCE_REF,
                consult_role="judgment_gap",
                admission_mode="autonomous",
            )
    assert outcome.refused is refused


@pytest.mark.offline
def test_ac9_giw_consult_contract_conflict() -> None:
    ledger = CursorDispatchLedger.instance()
    work_key = compute_work_key(
        root_id=_ROOT,
        source_ref=_SOURCE_REF,
        pickup_gid="G1",
        consult_role="judgment_gap",
        admission_mode="consult",
    )
    _admit(
        ledger,
        _req(dispatch_id="consult-a", thread_id="t-a"),
        source_repo="/mnt/torus/projects/universal-llm-gateway",
        contract="consult",
        work_key=work_key,
    )
    with pytest.raises(SourceRefConflict):
        _admit(
            ledger,
            _req(dispatch_id="consult-b", thread_id="t-b"),
            source_repo="/mnt/torus/projects/universal-llm-gateway",
            contract="consult",
            work_key=work_key,
        )
    forced = _admit(
        ledger,
        _req(dispatch_id="consult-c", thread_id="t-c"),
        source_repo="/mnt/torus/projects/universal-llm-gateway",
        contract="consult",
        work_key=work_key,
        force=True,
    )
    assert forced is not None


@pytest.mark.offline
def test_ac9_light_bounded_contract_conflict() -> None:
    ledger = CursorDispatchLedger.instance()
    work_key = compute_work_key(
        root_id=_ROOT,
        source_ref=_SOURCE_REF,
        pickup_gid="G1",
        consult_role="judgment_gap",
        admission_mode="light-bounded",
    )
    _admit(
        ledger,
        _req(dispatch_id="lb-a", thread_id="lb-t-a"),
        source_repo="/mnt/torus/projects/universal-llm-gateway",
        contract="light-bounded",
        work_key=work_key,
    )
    with pytest.raises(SourceRefConflict):
        _admit(
            ledger,
            _req(dispatch_id="lb-b", thread_id="lb-t-b"),
            source_repo="/mnt/torus/projects/universal-llm-gateway",
            contract="light-bounded",
            work_key=work_key,
        )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac10_consult_role_captured_at_admit(ledger_conn) -> None:
    work_key = _work_key(consult_role="judgment_gap")
    record_admit(
        ledger_conn,
        work_key=work_key,
        root_id=_ROOT,
        window_id="charter-6489-w1",
        dispatch_id="dispatch-w1",
        thread_id="thread-w1",
    )
    upsert_root(
        ledger_conn,
        _row(
            status=RootStatus.IDLE,
            wip_window_id=None,
            consult_role=None,
        ),
    )
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(status=RootStatus.IDLE, wip_window_id=None, consult_role=None),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
    )
    assert outcome.refused is True
    assert live_undispositioned_for_key(ledger_conn, work_key)


@pytest.mark.offline
def test_harvest_stamps_harvested_disposition(tmp_path: Path) -> None:
    conn = open_ledger_db(tmp_path / "harvest-ledger.sqlite")
    upsert_root(
        conn,
        _row(
            status=RootStatus.CONSULT_ADMITTED,
            wip_window_id="charter-6489-w1",
        ),
    )
    work_key = _seed_live_holder(conn)
    monkeypatch = pytest.MonkeyPatch()

    class _ConnWrapper:
        def __init__(self, inner):
            self._inner = inner
            self._closed = False

        def close(self) -> None:
            self._closed = True

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    wrapper = _ConnWrapper(conn)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_sequence.open_default_ledger",
        lambda: wrapper,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.window_sequence.write_cortex_mirror",
        lambda _row: None,
    )
    assert release_window_on_harvest(_ROOT, 1) is True
    record = find_record(
        conn,
        work_key=work_key,
        window_id="charter-6489-w1",
    )
    assert record is not None
    assert record.disposition == "harvested"
    conn.close()
    monkeypatch.undo()
