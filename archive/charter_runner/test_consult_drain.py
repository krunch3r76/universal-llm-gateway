"""Consult queue drain on root close (a:27395)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.consult_drain import (
    drain_consult_queue_for_root,
    drain_orphan_consults_under_closed_roots,
)
from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
    enqueue_consult,
    load_queue_row,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner import state_close, telemetry


def _ledger_row(*, root_id: str, status: RootStatus = RootStatus.IDLE) -> RootLedgerRow:
    return RootLedgerRow(
        root_id=root_id,
        status=status,
        pickup_gid="G1",
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
def test_drain_consult_queue_for_root_cancels_queued(ledger) -> None:
    row = _ledger_row(root_id="9001")
    upsert_root(ledger, row)
    enqueue_consult(ledger, row=row, consult_role="judgment_gap")

    drained = drain_consult_queue_for_root(
        ledger, "9001", reason="test_root_close"
    )
    assert len(drained) == 1
    assert drained[0].prior_status == "queued"
    assert drained[0].gid == "G1"

    queue_row = load_queue_row(ledger, "9001", "G1", "judgment_gap")
    assert queue_row is not None
    assert queue_row.status == "cancelled"


@pytest.mark.offline
def test_drain_consult_queue_for_root_idempotent_second_close(ledger) -> None:
    row = _ledger_row(root_id="9002", status=RootStatus.CLOSED)
    upsert_root(ledger, row)
    now = time.time()
    ledger.execute(
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, status, created_at, updated_at)
        VALUES ('9002', 'G1', 'judgment_gap', 'cancelled', ?, ?)
        """,
        (now, now),
    )
    ledger.commit()

    drained = drain_consult_queue_for_root(
        ledger, "9002", reason="test_second_close"
    )
    assert drained == []


@pytest.mark.offline
def test_drain_orphan_consults_under_closed_roots_only(ledger) -> None:
    closed = _ledger_row(root_id="9003", status=RootStatus.CLOSED)
    live = _ledger_row(root_id="6489", status=RootStatus.IDLE)
    upsert_root(ledger, closed)
    upsert_root(ledger, live)
    enqueue_consult(ledger, row=closed, consult_role="judgment_gap")
    enqueue_consult(ledger, row=live, consult_role="judgment_gap")

    drained = drain_orphan_consults_under_closed_roots(
        ledger, reason="test_orphan_cleanup"
    )
    assert len(drained) == 1
    assert drained[0].root_id == "9003"

    orphan = load_queue_row(ledger, "9003", "G1", "judgment_gap")
    live_row = load_queue_row(ledger, "6489", "G1", "judgment_gap")
    assert orphan is not None and orphan.status == "cancelled"
    assert live_row is not None and live_row.status == "queued"


@pytest.mark.offline
def test_apply_state_close_ledger_drains_and_second_close_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(state_close, "write_cortex_mirror", lambda _row: "")
    conn = open_ledger_db(tmp_path / "root-ledger.sqlite")
    row = _ledger_row(root_id="9004")
    upsert_root(conn, row)
    enqueue_consult(conn, row=row, consult_role="judgment_gap")
    conn.close()

    drained = state_close._apply_state_close_ledger("9004", reason="exhausted_hopper")
    assert len(drained) == 1

    verify = open_ledger_db(tmp_path / "root-ledger.sqlite")
    queue_row = load_queue_row(verify, "9004", "G1", "judgment_gap")
    assert queue_row is not None
    assert queue_row.status == "cancelled"
    verify.close()

    drained_again = state_close._apply_state_close_ledger(
        "9004", reason="exhausted_hopper"
    )
    assert drained_again == []


@pytest.mark.offline
def test_emit_consult_drained_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, dict]] = []

    async def _capture(signal: str, payload: dict) -> None:
        captured.append((signal, payload))

    monkeypatch.setattr(telemetry, "_emit", _capture)

    asyncio.run(
        telemetry.emit_consult_drained(
            root="9005",
            gid="G2",
            role="r_admit",
            queue_id=42,
            prior_status="queued",
            reason="root_close:exhausted_hopper",
        )
    )
    assert len(captured) == 1
    signal, payload = captured[0]
    assert signal == "manage.charter.tick.consult.drained"
    assert payload == {
        "root": "9005",
        "gid": "G2",
        "role": "r_admit",
        "queue_id": 42,
        "prior_status": "queued",
        "reason": "root_close:exhausted_hopper",
    }
