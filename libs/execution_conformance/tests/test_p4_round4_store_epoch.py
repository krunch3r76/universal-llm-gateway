"""Gate C M7 — persisted store epoch stability and fail-closed on recreate."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from execution_conformance.adapters import (
    AgentBusAdapter,
    CountingAdmitWrapper,
    StargatePipelineAdapter,
)
from execution_conformance.crash_hooks import dispatch_crash_after_reserve
from execution_conformance.ledger import ReferenceSqliteIntentLedger
from execution_conformance.reconciler import reconcile
from execution_conformance.store_epoch import persisted_store_epoch


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    from agent_bus_store.db.connection import init_db

    init_db()
    yield tmp_path / "bus.db"


def test_p4_store_epoch_stable_under_heavy_writes(bus_env) -> None:
    path = bus_env
    e1 = persisted_store_epoch(path)
    assert e1
    with closing(sqlite3.connect(path)) as conn:
        for i in range(200):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scratch_heavy (id INTEGER PRIMARY KEY, v TEXT)"
            )
            conn.execute("INSERT INTO scratch_heavy (v) VALUES (?)", (f"x{i}",))
        conn.commit()
    e2 = persisted_store_epoch(path)
    assert e1 == e2


def test_p4_store_epoch_recreate_blocks_retry(bus_env, tmp_path) -> None:
    path = bus_env
    e1 = persisted_store_epoch(path)
    ledger = ReferenceSqliteIntentLedger(tmp_path / "intents.db")
    wrapped = CountingAdmitWrapper(AgentBusAdapter())
    key = "epoch-recreate-key-abcdef12"
    dispatch_crash_after_reserve(
        ledger,
        wrapped,
        intent_id="intent-epoch",
        identity_key=key,
        admit_kwargs={"slug": "epoch"},
    )
    row = ledger.get("intent-epoch")
    assert row is not None
    assert row.store_epoch == e1
    path.unlink()
    from agent_bus_store.db.connection import init_db

    init_db()
    e2 = persisted_store_epoch(path)
    assert e2 and e2 != e1
    probe = CountingAdmitWrapper(AgentBusAdapter())
    result = reconcile(ledger, probe, "intent-epoch")
    assert result.blocked is True
    assert probe.admit_count == 0


def test_p4_stargate_new_tracker_new_epoch() -> None:
    a = StargatePipelineAdapter()
    b = StargatePipelineAdapter()
    assert a.store_epoch() != b.store_epoch()
