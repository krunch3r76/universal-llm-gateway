"""CONSULT_QUEUED heal when consult work_key is harvested (6486/6563)."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.admission import (
    CapsView,
    EnvFacts,
    decide,
)
from scripts.model_manager.ui.controller.charter_runner.admission.consult_heal import (
    HEAL_REASON,
    consult_work_key_is_harvested,
)
from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
    enqueue_consult,
    load_queue_row,
)
from scripts.model_manager.ui.controller.charter_runner import kernel_tick, telemetry
from scripts.model_manager.ui.controller.charter_runner.telemetry import emit_tick_transition
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    SeedConfirm,
    Transition,
    load_root,
    seed_from_confirm,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.window_sequence import (
    window_id_for,
)
from scripts.model_manager.ui.controller.charter_runner.work_key import compute_work_key
from scripts.model_manager.ui.controller.charter_runner.work_key_store import (
    record_admit,
    stamp_disposition,
)


def _open_caps() -> CapsView:
    return CapsView(
        allowed=True,
        skip_reason=None,
        stopped_reason=None,
        revise_ok=True,
        revise_reason=None,
    )


def _queued_row(*, root_id: str = "6563", gid: str = "G3") -> RootLedgerRow:
    return RootLedgerRow(
        root_id=root_id,
        status=RootStatus.CONSULT_QUEUED,
        pickup_gid=gid,
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri=f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md",
        consult_role="judgment_gap",
    )


@pytest.fixture
def ledger(tmp_path: Path):
    conn = open_ledger_db(tmp_path / "root-ledger.sqlite")
    yield conn
    conn.close()


@pytest.mark.offline
def test_decide_heals_when_consult_work_key_harvested() -> None:
    transition = decide(
        _queued_row(),
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            consult_work_key_harvested=True,
        ),
        _open_caps(),
    )
    assert transition == Transition.HEAL_CONSULT_QUEUED


@pytest.mark.offline
def test_decide_consult_queued_without_harvested_still_admits() -> None:
    transition = decide(
        _queued_row(),
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            consult_work_key_harvested=False,
        ),
        _open_caps(),
    )
    assert transition == Transition.ADMIT_CONSULT


@pytest.mark.offline
def test_consult_work_key_is_harvested_ledger_evidenced(ledger) -> None:
    row = seed_from_confirm(
        conn=ledger,
        confirm=SeedConfirm(
            root_id="6563",
            pickup_gid="G3",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri="cortex://notes/system/threads/6563-charter-scoreboard.md",
        ),
    )
    role = "judgment_gap"
    source_ref = "todo:6563-test"
    enqueue_consult(ledger, row=row, consult_role=role, source_ref=source_ref)
    queued = load_root(ledger, "6563")
    assert queued is not None
    assert queued.status == RootStatus.CONSULT_QUEUED

    work_key = compute_work_key(
        root_id="6563",
        source_ref=source_ref,
        pickup_gid="G3",
        consult_role=role,
        admission_mode="consult",
    )
    window_id = window_id_for("6563", 1)
    record_admit(
        ledger,
        work_key=work_key,
        root_id="6563",
        window_id=window_id,
        dispatch_id="dispatch-6563",
        thread_id="6564",
    )
    stamp_disposition(
        ledger,
        work_key=work_key,
        window_id=window_id,
        disposition="harvested",
    )
    upsert_root(ledger, replace(queued, status=RootStatus.CONSULT_QUEUED))

    assert consult_work_key_is_harvested(ledger, queued, role) is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_emit_tick_transition_stamps_running_code_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_emit(_signal: str, payload: dict[str, Any]) -> None:
        captured.append(payload)

    monkeypatch.setattr(telemetry, "_emit", fake_emit)
    monkeypatch.setattr(telemetry, "resolve_code_version", lambda: "deadbeef" * 5)

    await emit_tick_transition(
        root="6563",
        from_status="CONSULT_QUEUED",
        to_status="IDLE",
        transition="HEAL_CONSULT_QUEUED",
        reason=HEAL_REASON,
    )
    assert captured[0]["code_version"] == "deadbeef" * 5


@pytest.mark.offline
@pytest.mark.asyncio
async def test_kernel_heal_emits_transition(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = seed_from_confirm(
        conn=ledger,
        confirm=SeedConfirm(
            root_id="6563",
            pickup_gid="G3",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri="cortex://notes/system/threads/6563-charter-scoreboard.md",
        ),
    )
    role = "judgment_gap"
    source_ref = "todo:6563-heal"
    enqueue_consult(ledger, row=row, consult_role=role, source_ref=source_ref)
    work_key = compute_work_key(
        root_id="6563",
        source_ref=source_ref,
        pickup_gid="G3",
        consult_role=role,
        admission_mode="consult",
    )
    window_id = window_id_for("6563", 1)
    record_admit(
        ledger,
        work_key=work_key,
        root_id="6563",
        window_id=window_id,
        dispatch_id="dispatch-old",
        thread_id="6564",
    )
    stamp_disposition(
        ledger,
        work_key=work_key,
        window_id=window_id,
        disposition="harvested",
    )
    upsert_root(
        ledger,
        replace(
            load_root(ledger, "6563") or row,
            status=RootStatus.CONSULT_QUEUED,
            consult_role=role,
        ),
    )

    transitions: list[dict[str, Any]] = []

    async def capture_transition(**payload: Any) -> None:
        transitions.append(dict(payload))

    monkeypatch.setattr(kernel_tick, "emit_tick_transition", capture_transition)
    monkeypatch.setattr(kernel_tick, "open_default_ledger", lambda: ledger)

    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.env_snapshot import (
        EnvSnapshot,
    )

    env = EnvSnapshot(
        giw_holder_lease={"held": False},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up"},
        attendance_by_root={"6563": "autonomous"},
        scoreboard_pointer={},
        bus_tip_meta={},
        arc_lane_by_root={"6563": "layer"},
    )

    await kernel_tick.apply_kernel_tick_for_root(
        "6563",
        [],
        caps=CapStore(),
        workspace_root=tmp_path,
        env=env,
    )

    heal_events = [t for t in transitions if t.get("transition") == "HEAL_CONSULT_QUEUED"]
    assert len(heal_events) == 1
    assert heal_events[0]["from_status"] == "CONSULT_QUEUED"
    assert heal_events[0]["to_status"] == "IDLE"
    assert heal_events[0]["reason"] == HEAL_REASON
