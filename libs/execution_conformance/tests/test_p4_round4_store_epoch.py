"""Gate C M7 — read-only epoch witness, provisioning, rotation."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from execution_conformance.adapters import (
    AgentBusAdapter,
    CountingAdmitWrapper,
    StargatePipelineAdapter,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.store_epoch import (
    read_store_epoch,
    rotate_store_epoch,
)


def test_p4_store_epoch_stable_under_heavy_writes(bus_env) -> None:
    path = bus_env
    e1 = read_store_epoch(path)
    assert e1.status == "ok" and e1.epoch
    with closing(sqlite3.connect(path)) as conn:
        for i in range(200):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scratch_heavy (id INTEGER PRIMARY KEY, v TEXT)"
            )
            conn.execute("INSERT INTO scratch_heavy (v) VALUES (?)", (f"x{i}",))
        conn.commit()
    e2 = read_store_epoch(path)
    assert e1.epoch == e2.epoch


def test_p4_store_epoch_rotate_blocks_retry(bus_env, tmp_path) -> None:
    path = bus_env
    e1 = read_store_epoch(path).epoch
    assert e1
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    key = "epoch-recreate-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        CountingAdmitWrapper(AgentBusAdapter()),
        intent_id="intent-epoch",
        identity_key=key,
        admit_kwargs={"slug": "epoch"},
    )
    row = ledger.get("intent-epoch")
    assert row is not None
    assert row.store_epoch == e1
    rotate_store_epoch(path)
    e2 = read_store_epoch(path).epoch
    assert e2 and e2 != e1
    probe = CountingAdmitWrapper(AgentBusAdapter())
    result = reconcile(ledger, probe, "intent-epoch")
    assert result.blocked is True
    assert probe.admit_count == 0


def test_p4_stargate_shared_tracker_epoch_and_lock() -> None:
    from systems.pipeline.core.execution.async_tracker.tracker import (
        PipelineExecutionTracker,
    )

    tracker = PipelineExecutionTracker(event_bus=None)
    a = StargatePipelineAdapter(tracker)
    b = StargatePipelineAdapter(tracker)
    assert a.store_epoch() == b.store_epoch()
