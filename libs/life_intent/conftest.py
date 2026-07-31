"""Temp SQLite isolation for life_intent proposal store tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _life_intent_temp_proposal_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "life_intent_proposals.sqlite"
    monkeypatch.setenv("LIFE_INTENT_PROPOSAL_DB", str(db))
    from life_intent import proposal_store as store

    store.reset_connection_for_tests()
    store.clear_store()
    yield
    store.reset_connection_for_tests()
