"""Malformed nest_under pre-admit gate — a:27245 / agent-bus:6469 W8 class."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.identical_work_refire import (
    MALFORMED_NEST_SKIP_REASON,
    RefireGateContext,
    evaluate_identical_work_refire,
)
from scripts.model_manager.ui.controller.charter_runner.kernel_tick import (
    _parse_nest_under_from_parsed,
    _refire_context_for_row,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    Transition,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.window_sequence import (
    window_id_for,
)
from scripts.model_manager.ui.controller.charter_runner.work_key import compute_work_key
from scripts.model_manager.ui.controller.charter_runner.work_key_store import (
    record_admit,
)

_ROOT = "6469"
_SOURCE_REF = "todo:charter-malformed-orchestration-pre-admit"
_BUS_UUID = "0558c502-aaaa-bbbb-cccc-ddddeeeeffff"
_SHORT_HOLDER = "dispatch-w1"
_GIW_PAYLOAD = {
    "busy": True,
    "cursor_dispatches": {"dispatch_ids": [_SHORT_HOLDER]},
}


def _row(**overrides) -> RootLedgerRow:
    base = RootLedgerRow(
        root_id=_ROOT,
        status=RootStatus.CONSULT_ADMITTED,
        pickup_gid="G1",
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/6469-scoreboard.md",
        wip_window_id="charter-6469-w1",
    )
    return RootLedgerRow(**{**base.__dict__, **overrides})


def _work_key(*, consult_role: str = "judgment_gap") -> str:
    return compute_work_key(
        root_id=_ROOT,
        source_ref=_SOURCE_REF,
        pickup_gid="G1",
        consult_role=consult_role,
        admission_mode="consult",
    )


@pytest.fixture
def ledger_conn(tmp_path: Path):
    conn = open_ledger_db(tmp_path / "root-ledger.sqlite")
    upsert_root(conn, _row())
    yield conn
    conn.close()


def _seed_live_holder(
    conn,
    *,
    dispatch_id: str = _SHORT_HOLDER,
) -> str:
    work_key = _work_key()
    record_admit(
        conn,
        work_key=work_key,
        root_id=_ROOT,
        window_id=window_id_for(_ROOT, 1),
        dispatch_id=dispatch_id,
        thread_id="thread-w1",
        admitted_at=time.time() - 30.0,
    )
    return work_key


@pytest.mark.offline
@pytest.mark.asyncio
async def test_malformed_bus_uuid_nest_refused_before_admit(ledger_conn) -> None:
    """6469 W8: bus UUID nest_under + short live holder + same work_key ⇒ refuse."""
    work_key = _seed_live_holder(ledger_conn)
    outcome = await evaluate_identical_work_refire(
        ledger_conn,
        row=_row(),
        root_id=_ROOT,
        transition=Transition.ADMIT_CONSULT,
        source_ref=_SOURCE_REF,
        consult_role="judgment_gap",
        admission_mode="autonomous",
        giw_payload=_GIW_PAYLOAD,
        ctx=RefireGateContext(
            nest_under=_BUS_UUID,
            holder_dispatch_id=_SHORT_HOLDER,
        ),
    )
    assert outcome.refused is True
    assert outcome.skipped_reason == MALFORMED_NEST_SKIP_REASON
    assert outcome.work_key == work_key
    assert outcome.holder_dispatch_id == _SHORT_HOLDER


@pytest.mark.offline
@pytest.mark.asyncio
async def test_matching_short_nest_under_carve_out_admits(ledger_conn) -> None:
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
        ctx=RefireGateContext(
            nest_under=_SHORT_HOLDER,
            holder_dispatch_id=_SHORT_HOLDER,
        ),
    )
    assert outcome.refused is False
    assert outcome.carve_out == "nest"


@pytest.mark.offline
def test_parse_nest_under_from_wip_child_form() -> None:
    parsed = parse_checkpoint(
        "## WIP / In-flight\nnest_under child 0558c502\n\n## Next pickup\n1. G2"
    )
    assert _parse_nest_under_from_parsed(parsed) == "0558c502"


@pytest.mark.offline
def test_refire_context_populates_nest_and_holder(ledger_conn) -> None:
    work_key = _seed_live_holder(ledger_conn)
    body = (
        "## WIP / In-flight\n"
        f"nest_under child {_BUS_UUID}\n\n"
        "## Next pickup\n"
        "1. G2 — nested implement execution_id=ignored"
    )
    parsed = parse_checkpoint(body)
    ctx = _refire_context_for_row(
        ledger_conn,
        _row(),
        parsed,
        work_key=work_key,
    )
    assert ctx.nest_under == _BUS_UUID
    assert ctx.holder_dispatch_id == _SHORT_HOLDER


@pytest.mark.offline
def test_refire_gate_precedes_admit_in_kernel_tick() -> None:
    """evaluate_identical_work_refire runs before _admit_* (mark_admit_intent downstream)."""
    import inspect

    from scripts.model_manager.ui.controller.charter_runner import kernel_tick

    source = inspect.getsource(kernel_tick.apply_kernel_tick_for_root)
    refire_idx = source.index("evaluate_identical_work_refire")
    admit_consult_idx = source.index("await _admit_consult")
    admit_worker_idx = source.index("await _admit_worker")
    assert refire_idx < admit_consult_idx
    assert refire_idx < admit_worker_idx
