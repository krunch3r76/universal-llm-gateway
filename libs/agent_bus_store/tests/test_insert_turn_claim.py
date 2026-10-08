"""Claim-path edges for idempotent turn 1."""

from __future__ import annotations

import sqlite3

import pytest
from agent_bus_store.db import init_db, insert_turn
from agent_bus_store.db.turns import FirstTurnAuthorConflict


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


def test_claim_rejects_turn_one_from_other_agent(bus_db) -> None:
    insert_turn(
        thread="1",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="s",
        body="stored",
        idempotent_first_turn=True,
    )
    with pytest.raises(FirstTurnAuthorConflict) as exc:
        insert_turn(
            thread="1",
            from_agent="web-anthropic",
            to_agent="cursor",
            subject="s",
            body="other",
            idempotent_first_turn=True,
        )
    assert exc.value.existing_from == "cursor"


def test_integrity_error_fallback_returns_existing_id(
    bus_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner, _, _, claimed = insert_turn(
        thread="1",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="s",
        body="stored",
        idempotent_first_turn=True,
    )
    assert claimed is False
    skipped = {"n": 0}
    real_execute = sqlite3.Connection.execute

    def wrapped(self, sql, *args, **kwargs):
        if (
            isinstance(sql, str)
            and "turn_number = 1" in sql
            and sql.lstrip().upper().startswith("SELECT")
            and skipped["n"] == 0
        ):
            skipped["n"] += 1

            class _Empty:
                def fetchone(self):  # noqa: ARG002
                    return None

            return _Empty()
        return real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(sqlite3.Connection, "execute", wrapped)
    got, _, _, claimed_existing = insert_turn(
        thread="1",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="other",
        body="other",
        idempotent_first_turn=True,
    )
    assert got == winner
    assert claimed_existing is True
