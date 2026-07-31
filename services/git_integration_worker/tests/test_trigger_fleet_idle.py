"""Tests for fleet_idle predicate, defer semantics, and recur re-arm."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.git_integration_worker.trigger_service.config import fire_interval_s
from services.git_integration_worker.trigger_service.fire import reconcile_row
from services.git_integration_worker.trigger_service.fleet_idle import (
    FleetIdleSnapshot,
    FleetVerdict,
    begin_idle_pass,
    eval_fleet_idle,
    read_fleet_idle_memoized,
    reset_grace_tracker,
)
from services.git_integration_worker.trigger_service.models import (
    PREDICATE_FLEET_IDLE,
    STATUS_FIRING,
    STATUS_SCHEDULED,
    TriggerStoreError,
)
from services.git_integration_worker.trigger_service.store import TriggerStore

_PROMPT = "cortex://notes/system/threads/test-prompt.md"
_FLEET_ARGS = {
    "require_tick_empty": True,
    "require_dispatch_idle": True,
    "grace_s": 0,
}


class _StaticFleetReader:
    def __init__(self, snapshot: FleetIdleSnapshot) -> None:
        self._snapshot = snapshot
        self.read_count = 0

    def read(self) -> FleetIdleSnapshot:
        self.read_count += 1
        return self._snapshot


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    reset_grace_tracker()
    begin_idle_pass()
    return TriggerStore()


def _schedule_idle(
    store: TriggerStore,
    *,
    fire_at: datetime,
    recur_every_s: int | None = None,
    expires_at: datetime | None = None,
):
    return store.schedule(
        created_by="test",
        fire_at=fire_at,
        prompt_uri=_PROMPT,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args=_FLEET_ARGS,
        expires_at=expires_at,
        recur_every_s=recur_every_s,
    )


def test_fleet_idle_unmet_defers(store: TriggerStore) -> None:
    """AC: idle-unmet defers fire_at one pass; row stays scheduled."""
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    busy = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    reader = _StaticFleetReader(busy)

    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ):
        begin_idle_pass()
        claimed = store.claim_due(now=now)

    assert claimed is None
    updated = store.get(row.id)
    assert updated is not None
    assert updated.status == STATUS_SCHEDULED
    expected = (now + timedelta(seconds=fire_interval_s())).isoformat()
    assert updated.fire_at[:19] == expected[:19]


def test_fleet_idle_met_fires(store: TriggerStore) -> None:
    """AC: idle-met claims and fires path proceeds."""
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    idle = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    reader = _StaticFleetReader(idle)

    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ):
        begin_idle_pass()
        claimed = store.claim_due(now=now)

    assert claimed is not None
    assert claimed.id == row.id
    assert claimed.status == STATUS_FIRING


def test_fleet_idle_memoized_once_per_pass(store: TriggerStore) -> None:
    """AC6: fleet read happens once per pass even with multiple rows."""
    now = datetime.now(UTC)
    _schedule_idle(store, fire_at=now - timedelta(seconds=10))
    _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    idle = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    reader = _StaticFleetReader(idle)

    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ):
        begin_idle_pass()
        first = store.claim_due(now=now)
        assert first is not None
        store.mark_fired(first.id, execution_id="exec-1")
        second = store.claim_due(now=now)

    assert second is not None
    assert reader.read_count == 1


def test_rearm_on_terminal(store: TriggerStore) -> None:
    """AC: recurrence re-arms at terminal seam with coalesced fire_at."""
    now = datetime.now(UTC)
    recur_s = 1800
    row = _schedule_idle(
        store,
        fire_at=now - timedelta(minutes=5),
        recur_every_s=recur_s,
    )
    idle = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    reader = _StaticFleetReader(idle)
    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ):
        begin_idle_pass()
        claimed = store.claim_due(now=now)
    assert claimed is not None
    store.mark_fired(row.id, execution_id="exec-recur")
    store.mark_reconciled(row.id, terminal_status="completed")

    terminal_at = datetime.now(UTC)
    rearmed = store.rearm_recurring(row.id, terminal_at=terminal_at)
    assert rearmed is not None
    assert rearmed.status == STATUS_SCHEDULED
    assert rearmed.recur_every_s == recur_s
    assert rearmed.execution_id is None
    assert rearmed.terminal_status is None
    expected_fire = (terminal_at + timedelta(seconds=recur_s)).isoformat()
    assert rearmed.fire_at[:19] == expected_fire[:19]


def test_reconcile_row_rearms_recurring(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(minutes=1), recur_every_s=600)
    idle = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    reader = _StaticFleetReader(idle)
    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ):
        begin_idle_pass()
        claimed = store.claim_due(now=now)
    assert claimed is not None
    fired = store.mark_fired(row.id, execution_id="exec-r")

    mock_client = MagicMock()
    mock_client.poll.return_value = {"status": "completed", "archive_uri": None}

    with patch(
        "services.git_integration_worker.trigger_service.fire.verify_act_for_row",
        return_value={
            "act_status": "verified",
            "act_evidence_uri": None,
            "act_error": None,
        },
    ):
        result = reconcile_row(store, fired, client=mock_client)

    assert result is not None
    assert result.status == STATUS_SCHEDULED
    assert result.recur_every_s == 600


def test_cancel_stops_recurrence(store: TriggerStore) -> None:
    """AC: DELETE cancels scheduled recurring row — no future fires."""
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now + timedelta(hours=1), recur_every_s=900)
    cancelled = store.cancel(row.id)
    assert cancelled.status == "cancelled"
    assert store.claim_due(now=now + timedelta(hours=2)) is None


def test_fleet_idle_schedule_without_expires(store: TriggerStore) -> None:
    row = _schedule_idle(
        store,
        fire_at=datetime.now(UTC) + timedelta(hours=1),
        recur_every_s=1800,
    )
    assert row.predicate == PREDICATE_FLEET_IDLE
    assert row.expires_at is None
    assert row.recur_every_s == 1800


def test_trigger_terminal_still_requires_expires(store: TriggerStore) -> None:
    upstream = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(hours=1),
        prompt_uri=_PROMPT,
    )
    with pytest.raises(TriggerStoreError, match="expires_at required"):
        store.schedule(
            created_by="test",
            fire_at=datetime.now(UTC) + timedelta(hours=2),
            prompt_uri=_PROMPT,
            predicate="trigger_terminal",
            predicate_args={"trigger_id": upstream.id},
        )


def test_grace_s_holds_idle() -> None:
    reset_grace_tracker()
    snap = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    t0 = 1000.0
    assert eval_fleet_idle(snap, {"grace_s": 30}, now_monotonic=t0) is False
    assert eval_fleet_idle(snap, {"grace_s": 30}, now_monotonic=t0 + 29) is False
    assert eval_fleet_idle(snap, {"grace_s": 30}, now_monotonic=t0 + 30) is True


def test_tick_empty_queued_consults_do_not_block_default() -> None:
    """Queued-only consults: narrow tick_empty true, predicate passes without strict arg."""
    reset_grace_tracker()
    snap = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=True,
        tick_empty=True,
        tick_empty_strict=False,
        cursor_auto_idle=True,
    )
    assert eval_fleet_idle(snap, {"grace_s": 0}) is True


def test_tick_empty_admitted_consult_blocks() -> None:
    reset_grace_tracker()
    snap = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=True,
        tick_empty=False,
        tick_empty_strict=False,
        cursor_auto_idle=True,
    )
    assert eval_fleet_idle(snap, {"grace_s": 0}) is False


def test_tick_empty_admitted_root_blocks() -> None:
    reset_grace_tracker()
    snap = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=True,
        tick_empty=False,
        tick_empty_strict=False,
        cursor_auto_idle=True,
    )
    assert eval_fleet_idle(snap, {"grace_s": 0}) is False


def test_block_on_queued_consults_restores_strict() -> None:
    reset_grace_tracker()
    snap = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=True,
        tick_empty=True,
        tick_empty_strict=False,
        cursor_auto_idle=True,
    )
    assert eval_fleet_idle(snap, {"grace_s": 0, "block_on_queued_consults": True}) is False


def test_charter_tick_empty_narrow_vs_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from libs.charter_runner_store.db import open_ledger_db
    from services.git_integration_worker.trigger_service import fleet_idle as fi

    db_path = tmp_path / "root-ledger.sqlite"
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))

    conn = open_ledger_db(db_path)
    now = time.time()
    conn.execute(
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, status, created_at, updated_at)
        VALUES ('r1', 'G1', 'judgment_gap', 'queued', ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()

    narrow, strict, undetermined = fi._charter_tick_empty()
    assert undetermined is False
    assert narrow is True
    assert strict is False

    conn = open_ledger_db(db_path)
    conn.execute(
        """
        UPDATE consult_queue SET status = 'admitted', updated_at = ?
        WHERE root_id = 'r1'
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    narrow, strict, undetermined = fi._charter_tick_empty()
    assert narrow is False
    assert strict is False

    conn = open_ledger_db(db_path)
    conn.execute(
        """
        INSERT INTO root_ledger
          (root_id, status, attendance, scoreboard_uri, updated_at)
        VALUES ('r2', 'ADMITTED', 'attended', 'cortex://test', ?)
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    narrow, strict, undetermined = fi._charter_tick_empty()
    assert narrow is False
    assert strict is False


def test_block_on_queued_consults_validates(store: TriggerStore) -> None:
    with pytest.raises(TriggerStoreError, match="block_on_queued_consults"):
        store.schedule(
            created_by="test",
            fire_at=datetime.now(UTC) + timedelta(hours=1),
            prompt_uri=_PROMPT,
            predicate=PREDICATE_FLEET_IDLE,
            predicate_args={"block_on_queued_consults": "yes"},
        )
