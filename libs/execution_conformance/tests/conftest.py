"""Shared fixtures — provision owner store epoch after SQLite bootstrap."""

from __future__ import annotations

import pytest
from execution_conformance.store_epoch import provision_store_epoch


@pytest.fixture()
def bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    from agent_bus_store.db.connection import init_db

    init_db()
    provision_store_epoch(db)
    yield db


@pytest.fixture()
def cdp_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "gateway"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    from systems.frontier_consult.cdp_generate_inflight_ledger import _db_path

    leg_path = _db_path()
    leg_path.parent.mkdir(parents=True, exist_ok=True)
    if not leg_path.exists():
        leg_path.touch()
    provision_store_epoch(leg_path)
    yield tmp_path
