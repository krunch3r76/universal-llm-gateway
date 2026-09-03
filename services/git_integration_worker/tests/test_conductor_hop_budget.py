"""R6: conductor hop budget enforcement."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    maybe_fire_conductor_hop_reactor,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    _PARK_REASON_CRASH_CAP,
    _PARK_REASON_MISSION_CAP,
    _PARK_REASON_NO_PROGRESS_CAP,
    HopBudgetConfig,
    build_parked_transport_body,
    evaluate_hop_budget,
    load_hop_budget_config,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_WORK_KEY = "todo:hop-budget-fixture"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "9964",
        "model": "cursor/composer-2.5",
        "dispatch_id": "pred-budget-1",
        "execution_id": "exec-pred-budget-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_and_terminal(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    hop_seq: int,
    hop_from: str,
    hop_reason: str,
    closeout_tokens: list[str] | None = None,
    terminal_status: str = "completed",
    record_patch: dict | None = None,
) -> dict:
    req = _req(dispatch_id=dispatch_id)
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract="light-bounded",
        source_repo="/repo",
        lease_key="/repo",
        work_key=_WORK_KEY,
        source_ref=_WORK_KEY,
        hop_seq=hop_seq,
        hop_from=hop_from,
        hop_reason=hop_reason,
    )
    patch_body = {"packet_kind": "conductor", "lane": "B"}
    if closeout_tokens is not None:
        patch_body["closeout_stop_tokens"] = closeout_tokens
    if record_patch:
        patch_body.update(record_patch)
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=patch_body)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=terminal_status)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return {k: row[k] for k in row.keys()}


def _tight_config(**overrides: object) -> HopBudgetConfig:
    base = {
        "crash_cap_per_row": 3,
        "no_progress_cap": 2,
        "mission_cap": 24,
        "crash_backoff_s": (30.0, 120.0, 300.0),
        "reactor_grace_s": 120.0,
    }
    base.update(overrides)
    return HopBudgetConfig(**base)


def test_load_hop_budget_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", raising=False)
    monkeypatch.delenv("CONDUCTOR_HOP_NO_PROGRESS_CAP", raising=False)
    monkeypatch.delenv("CONDUCTOR_HOP_MISSION_CAP", raising=False)
    cfg = load_hop_budget_config()
    assert cfg.crash_cap_per_row == 3
    assert cfg.no_progress_cap == 2
    assert cfg.mission_cap == 24
    assert cfg.crash_backoff_s == (30.0, 120.0, 300.0)
    assert cfg.reactor_grace_s == 120.0


def test_load_hop_budget_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", "5")
    monkeypatch.setenv("CONDUCTOR_HOP_NO_PROGRESS_CAP", "1")
    monkeypatch.setenv("CONDUCTOR_HOP_MISSION_CAP", "10")
    cfg = load_hop_budget_config()
    assert cfg.crash_cap_per_row == 5
    assert cfg.no_progress_cap == 1
    assert cfg.mission_cap == 10


def test_planned_row_hop_budget_ok() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _admit_and_terminal(
        ledger,
        dispatch_id="pred-budget-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={
            "hop_entry_gate": "G4",
            "hop_witnessed_done": [],
        },
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.backoff_s == 0.0


def test_mission_cap_parks() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"hop-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"hop-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="hop-4",
        hop_seq=4,
        hop_from="hop-3",
        hop_reason="planned",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(mission_cap=3),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_MISSION_CAP


def test_crash_cap_parks() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 3):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"crash-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"crash-{idx - 1}",
            hop_reason="crash",
            terminal_status="failed",
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="crash-3",
        hop_seq=3,
        hop_from="crash-2",
        hop_reason="crash",
        terminal_status="failed",
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_CRASH_CAP


def test_crash_backoff_under_cap() -> None:
    ledger = CursorDispatchLedger.instance()
    row = _admit_and_terminal(
        ledger,
        dispatch_id="crash-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="crash",
        terminal_status="failed",
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset(),
        config=_tight_config(crash_cap_per_row=3),
    )
    assert verdict.ok is True
    assert verdict.park is False
    assert verdict.backoff_s == 30.0


def test_no_progress_cap_parks() -> None:
    ledger = CursorDispatchLedger.instance()
    witness: list[str] = []
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"plan-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"plan-{idx - 1}",
            hop_reason="planned" if idx > 1 else "spawn",
            closeout_tokens=["ROW_HOP"],
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": witness},
        )
    row = _admit_and_terminal(
        ledger,
        dispatch_id="plan-4",
        hop_seq=4,
        hop_from="plan-3",
        hop_reason="planned",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": witness},
    )
    verdict = evaluate_hop_budget(
        row,
        closeout_tokens=frozenset({"ROW_HOP"}),
        config=_tight_config(no_progress_cap=2),
    )
    assert verdict.park is True
    assert verdict.reason == _PARK_REASON_NO_PROGRESS_CAP


def test_build_parked_transport_body() -> None:
    body = build_parked_transport_body(
        reason=_PARK_REASON_CRASH_CAP,
        hop_seq=3,
    )
    assert "stop: PARKED_TRANSPORT" in body
    assert f"reason: {_PARK_REASON_CRASH_CAP}" in body
    assert "hop_seq: 3" in body


@pytest.mark.asyncio
async def test_maybe_fire_parks_on_budget_exhaustion() -> None:
    ledger = CursorDispatchLedger.instance()
    for idx in range(1, 4):
        _admit_and_terminal(
            ledger,
            dispatch_id=f"park-{idx}",
            hop_seq=idx,
            hop_from="spawn" if idx == 1 else f"park-{idx - 1}",
            hop_reason="crash",
            terminal_status="failed",
            record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
        )
    with (
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget.load_hop_budget_config",
            return_value=_tight_config(crash_cap_per_row=3),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(),
        ) as post_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget.default_park_poster",
        ) as park_poster,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget.page_hop_budget_parked",
            AsyncMock(return_value=True),
        ),
        patch(
            "services.git_integration_worker.cursor_sdk_hop_events.emit_frontier_sdk_conductor_hop_parked",
        ) as parked_event,
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="park-3")
    post_mock.assert_not_called()
    park_poster.assert_called_once()
    parked_event.assert_called_once()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id='park-3'"
        ).fetchone()
    data = json.loads(row["record_json"])
    assert data.get("hop_parked") is True
    assert data.get("hop_park_reason") == _PARK_REASON_CRASH_CAP


@pytest.mark.asyncio
async def test_maybe_fire_planned_hop_no_backoff() -> None:
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="pred-budget-1",
        hop_seq=1,
        hop_from="spawn",
        hop_reason="spawn",
        closeout_tokens=["ROW_HOP"],
        record_patch={"hop_entry_gate": "G4", "hop_witnessed_done": []},
    )
    with (
        patch("asyncio.sleep", AsyncMock()) as sleep_mock,
        patch(
            "services.git_integration_worker.cursor_sdk_closeout.conductor_hop.post_conductor_hop_team_dispatch",
            AsyncMock(return_value=(True, {"dispatch_id": "succ-2"})),
        ),
    ):
        await maybe_fire_conductor_hop_reactor(dispatch_id="pred-budget-1")
    sleep_mock.assert_not_called()
